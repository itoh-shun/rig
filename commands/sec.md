---
description: "rig/sec — ホワイトハッカー pack。攻撃者視点でコードを能動的に監査(audit)し、確認済み脆弱性を PoC 回帰テスト付きで塞ぎ(fix)、定期再スキャンで見張る(monitor)。倫理境界=自プロダクト/許可済み環境・静的+ローカル検証のみ・DAST 既定範囲外。"
argument-hint: "[audit|fix|monitor] [対象パス/機能・所見] [--plan] [--autonomous] [--until …|--times N]"
---

# rig/sec — セキュリティ（ホワイトハッカー）モード 🛡️🔍

**まず `rig:engine` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal・facet 配置順・知識層注入）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。dev / magi / loop と同じ engine を「攻撃者視点の防御」に使う security pack。

```
$ARGUMENTS
```

## 倫理境界（最初に確認・逸脱不可）

- 対象は**自プロダクトのコード**、または**明示的に検証を許可されたローカル/ステージング環境**のみ。スコープ外・第三者・本番へ技法を向けない。
- **静的解析＋ローカル検証まで。** 動いているサービスへ攻撃トラフィックを送る動的スキャン（DAST）は既定で範囲外（`.rig/security-targets.json` の allowlist がある場合のみ、そのホストに限る）。
- スコープが曖昧なら一言だけ確認してから始める（捏造しない）。

## サブモード

| 引数 | recipe | 何をするか |
|---|---|---|
| `audit`（既定） | `security-audit` | 攻撃者視点で既存コードを能動監査。threat-model→（SAST/SCA/secret センサー）→exploit 探索。刺さった経路だけ Confirmed で報告（read-only）。 |
| `fix` | `pentest-fix` | audit の Confirmed 所見を1件ずつ塞ぐ。PoC を「攻撃が失敗すること」の回帰テスト化→canonical 修正→re-exploit 失敗まで accept 不可。 |
| `monitor` | `security-monitor` | 定期再スキャンで脆弱性を見張る。SAST/SCA/secret 再実行→新規所見トリアージ→（opt-in で）fix キック。停止条件・上限必須。 |

引数の先頭が `audit`/`fix`/`monitor` ならそれを、無ければ `audit` を既定に、残りを対象として PARSE する。

## やること

対象を選んだ recipe に渡す。手順本体は各 instruction が正本：
- `facets/instructions/security-audit`（スコープ宣言→threat-model→センサー→exploit 探索→集約）
- `facets/instructions/pentest-fix`（PoC 回帰テスト化→canonical 修正→re-exploit→独立レビュー→acceptance）
- `facets/instructions/security-monitor`（各 tick の再スキャン→差分トリアージ→報告→次 tick 予約）

- **実作業は subagent が回す**（context-minimal）。長いコードを親に引き込まない。
- **所見は捏造しない**：刺さると示せた Confirmed と、未確認の Suspected（情報不足）を必ず分ける。低確信の重大判定は禁止。
- **fix は「直った」の自己申告を許さない**：元の PoC が再実行で失敗する（＝穴が塞がった）まで gate が accept を止める。

## 決定論センサー（rig はツールを実行しない＝出力を渡す）

**ワンコマンド（`run`＝回して取り込むまで一発。ローカル静的スキャンのみ・外部通信なし）**：
```
python3 scripts/sast_adapter.py run semgrep --path . --apply <task-id>   # SAST → sast_findings_clear
python3 scripts/sast_adapter.py run pip-audit --apply <id>               # SCA  → sca_findings_clear
python3 scripts/sast_adapter.py run npm-audit --apply <id>
python3 scripts/sast_adapter.py run trivy --path . --apply <id>
python3 scripts/sast_adapter.py run claude-security --apply <id>         # 最新の CLAUDE-SECURITY-*/…jsonl を自動発見 → deep_scan_findings_clear
```
（ツール固有フラグは `-- <args>` で後置。ツール未インストール時は pipe-in 形にフォールバックを案内。）

**pipe-in（rig にツールを実行させたくない／CI で別に回す場合）**：
```
semgrep --json … > out.json ; python3 scripts/sast_adapter.py semgrep out.json --apply <id>
python3 scripts/sast_adapter.py sarif out.sarif --apply <id>            # SARIF (CodeQL/semgrep --sarif/managed export)
python3 scripts/sast_adapter.py claude-security CLAUDE-SECURITY-<ts>/CLAUDE-SECURITY-RESULTS.jsonl --apply <id>
```

`sast_findings_clear`/`sca_findings_clear`/`deep_scan_findings_clear`/`exploit_reproduced_then_closed` は optional criterion＝`.rig/gates.json` の `extra_criteria` に登録したプロジェクトで gate が要求する。**`claude-security` はリポジトリ全体・複数ファイル横断を見る**ので、diff スコープの gated レビューが構造的に見逃す「変更が信頼する未変更コードの欠陥」を補完する（`benchmarks/hard-tasks` の実測で判明した盲点への実効策）。

## flag

- `--plan` … 探索/修復構成を提示して停止（ドライラン）。
- `--autonomous` … monitor/fix の後続委譲の step ゲートを省くだけ。accept の capture ゲートは解除されない。
- `--until <条件>` / `--times N` … monitor の停止条件（必須）。

## 例

```
/rig:sec audit src/auth            # 認証周りを攻撃者視点で監査
/rig:sec 決済ハンドラを監査して      # audit 既定
/rig:sec fix                       # 直前 audit の Confirmed 所見を gated 修復
/rig:sec monitor --times 7 依存の新規CVEを日次で   # 定期再スキャン(7回上限)
/rig:sec --plan src/api            # 探索構成だけ確認
```

## 差別化（rig を通す価値）

素の AI に「脆弱性を探して/直して」と頼むと、印象論の羅列や、公開テストだけ通す弥縫策（narrow fix＝少しずらすと再び刺さる silent security defect）が返りやすい。security pack は `security-findings` 契約で Confirmed/Suspected を分離し、`pentest-fix` が PoC 回帰テストで修正前後の赤→緑を要求し、独立検証者と acceptance-gate が「本当に刺さる/塞がった」を機械的に問う。この差は `benchmarks/security-tasks/` が silent-defect 率で定量化する。

## run-continuity（SKILL.md §6）

RUN 中は各ターン冒頭に次の run-status ヘッダを1行必ず再掲すること。中断・質疑・tool 出力の直後でも省かない:

```
▸ rig | recipe: <security-audit|pentest-fix|security-monitor> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
