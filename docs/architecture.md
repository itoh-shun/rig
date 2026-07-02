# rig architecture — 目利き向けの1ページ

自作でエージェント・オーケストレーションを組んでいる人が「どこが本気か」を最短で確認するためのページ。主張ごとに**その場で叩けるコマンド**を添える。散文の主張はここでは価値ゼロ、動くものだけ並べる。

## 全体像（60秒）

rig は「LEGO 式ハーネス合成機」。ブリック（persona / instruction / pattern / policy / knowledge / recipe）を**呼び出しごとに**合成して、そのタスク専用のエージェント・ハーネスを組む。設計原則は一貫して1つ：

> **determinism-by-gate** — 経路（生成が何回やり直したか・どの subagent が動いたか）は非決定的でよい。**確実さはゲートに持たせ、最終出力の品質を一定水準へ収束させる。**

以下、この原則が「散文のお願い」ではなく「機構」になっている箇所を7点。

## 1. RESOLVE は散文ではなくコード（golden で固定）

named recipe の合成規則（extends マージ・diff サイズ条件・flag 優先順位・manifest 反映）は `orchestrate.py plan --json` が**一次実装**。散文（SKILL.md §4）はフォールバック定義で、両者の同一性は selftest の golden シナリオが CI で保証する。

```bash
python3 scripts/orchestrate.py plan release-flow --json --with "--design" --diff-git
python3 scripts/orchestrate.py selftest   # 85 チェック・同入力→同遷移の決定論証明
```

## 2. 制御ループもコード（状態機械・opt-in）

step 遷移・ゲート・リトライ上限 K・停止・エスカレーションは決定論ランナーが強制する。モデルは「作業」をするが「次に何をするか」は決めない。

```bash
python3 scripts/orchestrate.py run review-only --provider mock --verifier-provider mock
# → START → EXEC（別プロセス生成）→ 並列検証 → ADVANCE/RETRY → DONE/ESCALATE
```

- DAG 並列（`needs:`）・judge-panel（`--generators a,b,c`＝複数モデル生成→勝者選択）・モデル混成クォーラム（`--verifier-providers a,b,c`＝異種モデルの票決）を同じ状態機械の上で。
- 状態は `run-state.json` に永続＝プロセス死・コンテキスト圧縮を跨いで再開可能。

## 3. 検証役は「書けない」（権限を argv で強制）

採点者≠生成者は、①別プロセス、②**読み取り専用権限の固定付与**、の2段で強制する。verifier ロールの CLI には rig が権限フラグを必ず付ける：

```
claude → --allowedTools Read,Grep,Glob
codex  → --sandbox read-only
```

「レビュアーはコードを書かないでください」という**プロンプトのお願いに依存しない**。selftest N が argv を golden 検証。

## 4. 生成は隔離空間で（`--isolate`）

```bash
python3 scripts/orchestrate.py run <recipe> --provider rig --isolate
```

run を使い捨て git worktree＋専用 branch に隔離し、後始末は決定論規則（selftest X）：

| 終了状態 | 後始末 |
|---|---|
| DONE・クリーン・commit あり | 元 branch へ **ff 合流**して撤収 |
| DONE・変更なし | 撤収のみ |
| 未達 / dirty / 非 ff | worktree と branch を**保全**（人が検分） |

非決定的な生成過程は作業ツリーに触れない。**ゲートを通った成果だけが合流する** — determinism-by-gate の空間版。

## 5. 判定役自身を測る（ここが多分いちばん珍しい）

「レビュアーを置く」は誰でもやる。rig は**レビュアーの検出力を実測する**：

```bash
/rig:drill            # 既知のバグを意図的に混ぜ、各レビュアーの検出率・誤検知を測定
/rig:drill --replay   # persona 変更前後で同じ題材を再判定（判定役のスナップショットテスト）
python3 scripts/orchestrate.py runs --personas   # 全 run の票を集計。5票以上 REJECT ゼロ＝ゴム印疑いを警告
```

全 run のゲート判定は `.rig/runs.jsonl` に落ち、同じ step で2回詰まればギャップ処方箋（能力の獲得提案）が出る。**ループが自分の判定品質を監視する**ループになっている。

## 6. 能力の取り込みに免疫系がある

`/rig:import` は外部 skill の取り込みに **⓪発見→①取得→②検疫（prompt-injection スキャン・データは引用として読む）→③判断（license 対応表）→④確認→⑤試用ゲート→⑥lock（SHA-256・sourceRef）→⑦検証** のパイプラインを通す。更新は 3-way diff（`--check-updates` / `--update`）。取り込んだ能力も `/rig:export` で還元できる。

## 7. ハーネス自身がテスト対象

```bash
python3 scripts/validate.py   # 34 チェック：recipe スキーマ・persona 契約・wiki 賞味期限・目録ドリフト・グラフ整合
python3 scripts/orchestrate.py graph --focus review-gate   # 型付きブリック・グラフ（frontmatter から導出・手書きしない）
```

- ブリック間の関係（injects / extends / uses-* / gated-by / mirrors 等11種）は**手書きせず導出**する＝レジストリが腐らない。
- validate は実際に踏んだバグの再発防止検査を持つ（frontmatter YAML 破壊・予約コマンド名など）。CI は validate＋selftest の両輪。

## 設計上の意図的な選択

- **重い DSL エンジンを持たない。** ブリックは Markdown＋frontmatter、ランナーは単一ファイルの Python（依存は PyYAML のみ）。Claude Code のネイティブ機構（skill / agents / hooks）に寄生し、再発明しない。
- **全自動を既定にしない。** 書き込み・push・import の確認ゲートは `--autonomous` でも外れない。判定の甘い自己改善ループは「間違いを高速に学習する装置」になるため、昇格（学び→wiki、試用→lock）には必ず人の確認を挟む。
- **散文とコードが食い違ったら、コード側（selftest）を先に直す。** 一次実装はコード、散文は説明。

## 5分で試す

```bash
python3 scripts/validate.py && python3 scripts/orchestrate.py selftest   # まず健全性
python3 scripts/orchestrate.py plan review-only --json                    # 合成の決定論を見る
python3 scripts/orchestrate.py run review-only --provider mock --verifier-provider mock --isolate
python3 scripts/orchestrate.py graph                                      # ブリック網の型付きグラフ
/rig:party                                                                # 判定履歴が実データの RPG シートに
```
