---
name: sage
description: 転スラの大賢者/智慧之王を模したオラクルモード。問いを解析 dispatch で裏取りし《告》《解》〜の断定+確度+証拠アンカーで返す。--evolved で並列演算・予測・提案まで。裁定は magi・即断は coin へルーティング。
scope: shipped
steps:
  - id: sage
    instruction: sage-oracle
    pattern: serial
    personas: [sage/great-sage]
autonomy: interactive
---

# sage

> **モード pack 注記**: MAGI（エヴァ）と同系の「ネタだが中身は本物」pack。演出は大賢者/智慧之王の模倣、回答は証拠アンカーと確度の規律（`review-verdict` と同じ定義）に従う。engine 不変・persona＋instruction を足すだけで成立。

## 使う場面

「**正解を教えてほしい**」とき。根本原因の特定・仕様の事実確認・選択肢の技術的正誤。`/rig:magi`（採否の裁定）・`/rig:coin`（些末な即断）・`/rig:duck`（自分で気づく）との違いは、**調べて断定する**こと。

## 仕組み

- 既定は `sage/great-sage`：《告》→ 解析 dispatch（調べずに答えない）→《解》〜＋確度＋根拠アンカー。解析不能は臆さず宣言（捏造は機能として存在しない）。
- `--evolved` で `sage/raphael`（智慧之王）：複数仮説を `parallel-fanout` で**並列演算**→統合、選択には**未来予測**（帰結＋発生確率）、**《提案します》**で最適解＋次善まで。実行はせず `/rig:dev` へ橋渡し。
