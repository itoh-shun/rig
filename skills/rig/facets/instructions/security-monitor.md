# instruction: security-monitor

## facet: instruction / security-monitor

コードベースを**定期的に再スキャンして脆弱性を検知し続ける**監視ループの手順書。`loop`（watch/poll/repeat）に security の中身を載せたもの。新しい制御は発明せず、**`patterns/autonomous-loop`（`ScheduleWakeup`）で次の tick を予約する**だけ。攻撃トラフィックは送らない＝**スキャン（観測）専用**。

### 何を回すか（各 tick）

1. **決定論センサーの再実行**（プロジェクトが用意しているもの）：
   - SAST: `semgrep --json` → `scripts/sast_adapter.py semgrep`
   - SCA: `pip-audit`/`npm audit`/`trivy fs` → `sast_adapter.py <tool>`（新規 CVE の advisory 更新はここで拾う）
   - 秘密: `workbench.py scan-secrets`
2. **差分トリアージ** — 前 tick からの新規所見だけを取り出す（既知・抑制済みは `suppression-memory` に従い黙らせる＝毎回同じ指摘で騒がない）。
3. **新規 Confirmed があれば** — severity 付きで報告し、（opt-in なら）`pentest-fix` を1件キックして gated 修復に繋ぐ。重大でなければ所見を積むだけ。
4. **報告** — 各 tick を1行以上で報告（沈黙で回り続けない）。「新規0件・既知N件・次 tick 予約」を明示。

### ガード（`loop` と同じ規律）

- **停止条件 or 上限が必須**（`--until`/`--times`/明示停止）。「無限監視」に勝手に入らない。
- **opt-in の自律ループ**：`patterns/autonomous-loop` は `--autonomous` 明示時のみ。時間駆動は `ScheduleWakeup` の `delaySeconds` 規約（ウォーム 270 秒・コールド 1200 秒以上・**300 秒は禁忌**）。CVE フィードの更新は分単位で変わらないので、依存監視だけなら数時間〜日次で十分。
- **書込/push を伴う修復（`pentest-fix`）は tick ごとに委譲先の step ゲートで確認**。`--autonomous` でも accept の capture ゲートは解除されない。
- **スキャン専用の境界**：外部の動いているサービスへ攻撃リクエストを送らない（DAST は範囲外）。監視対象は自リポジトリのコードと依存。

### 使い分け

- 新規コミット契機で回したい → CI 側で `sast_adapter.py` を回し、rig は結果のトリアージに徹する。
- 時間契機（依存の新規 CVE を拾う）→ このループを日次等で回す。`/rig:loop --every` の外側スケジューラと重ねてもよい。
