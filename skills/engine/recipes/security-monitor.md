---
name: security-monitor
description: コードベースを定期的に再スキャンして脆弱性を検知し続ける監視ループ recipe。loop に security の中身を載せたもの。SAST/SCA/secret 再スキャン→新規所見トリアージ→（opt-in で）pentest-fix キック。スキャン専用で攻撃トラフィックは送らない。停止条件・上限必須。
scope: shipped
steps:
  - id: monitor
    instruction: security-monitor
    pattern: serial
    personas: [orchestrator]
autonomy: interactive
---

# security-monitor

> **セキュリティ pack 注記**: security pack の監視 recipe。新しい制御は発明せず、`loop`（`patterns/autonomous-loop`＝`ScheduleWakeup`）に security の中身（再スキャン→トリアージ）を載せる。`/rig:sec monitor` から起動。

## 使う場面

**終わりのない見張り＝脆弱性を定期的に検知し続けたい**時。「AI から監視する」の中身。

- 「依存の新規 CVE を日次で拾って、重大なら直して」
- 「main に入るたび SAST を回して新規所見だけ報告して」
- 「この脆弱性、混入しないか見張って」

## 各 tick で回すこと

1. **決定論センサー再実行** — `semgrep`（SAST）/ `pip-audit`・`npm audit`・`trivy fs`（SCA）/ `workbench.py scan-secrets`（秘密）。出力を `scripts/sast_adapter.py` に渡してゲート基準へ正規化。
2. **差分トリアージ** — 前 tick からの**新規所見だけ**取り出す（既知・抑制済みは `suppression-memory` で黙らせる＝毎回同じ指摘で騒がない）。
3. **新規 Confirmed の処理** — severity 付きで報告。重大かつ opt-in なら `pentest-fix` を1件キックして gated 修復へ。そうでなければ積むだけ。
4. **tick 報告** — 「新規N件・既知M件・次 tick 予約」を1行以上で必ず報告。

## ガード（`loop` と同じ規律・逸脱不可）

- **停止条件 or 上限が必須**（`--until`/`--times`/明示停止）。無限監視に勝手に入らない。
- **opt-in の自律ループ**：`patterns/autonomous-loop` は `--autonomous` 明示時のみ。`delaySeconds` は規約に従う（ウォーム 270 秒・コールド 1200 秒以上・**300 秒は禁忌**）。CVE フィードは分単位で変わらないので依存監視は数時間〜日次で十分。
- **書込/push を伴う修復は tick ごとに step ゲートで確認**（`--autonomous` でも accept の capture ゲートは解除されない）。
- **スキャン専用**：外部の動いているサービスへ攻撃リクエストを送らない（DAST は範囲外）。監視対象は自リポジトリのコードと依存。
