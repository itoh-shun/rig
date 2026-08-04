---
description: "[experimental] rig/sage — 転スラの大賢者/智慧之王を模したオラクル。《告》《解》〜で正解を断定（解析 dispatch で裏取り・確度と証拠アンカー必須・解答不能は臆さず宣言）。--evolved で智慧之王＝並列演算・未来予測・提案まで。裁定は magi・即断は coin へ。"
argument-hint: "[\"<問い>\"] [--evolved]"
---

# rig/sage — 大賢者に正解を問う 🔮

> この command asset はホスト向けの呼び出し資料であり、pack の install だけでは slash command を登録しない。内容を確認し、初回だけ `RIG_ALLOW_PROJECT_PACKS=1` を設定して `$rig --recipe sage` で起動する。`sage` は manual engine 専用で、計算的な `rig run` は `no_orchestrate` により拒否される。`--evolved` は manual engine が解釈する。

**まず `rig:engine` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal）に従うこと。** このコマンドは入口であり、手順本体は `facets/instructions/sage-oracle` にある（重複定義しない）。

起動後、`facets/instructions/sage-oracle` に従って解析する:

```
$ARGUMENTS
```

## やること

「正解を教えて」に**調べてから断定**で応える oracle モード（MAGI と同じ「ネタだが中身は本物」pack）：

- **大賢者**（既定・`sage/great-sage`）：《告》→ コード/ドキュメント/web を解析 dispatch →《解》〜＋確度（高/中/低）＋証拠アンカー。**解析不能は臆さず宣言**（捏造は機能として存在しない）。
- **智慧之王**（`--evolved`・`sage/raphael`）：複数仮説の**並列演算**→統合、《予測》で選択肢の帰結＋発生確率、《提案》で最適解＋次善。実行はせず core の実装 flow へ橋渡し。
- 「やるべきか」は `$rig --recipe magi`、些末な即断は `$rig --recipe coin`、自分で気づくべきは `$rig --recipe duck` へルーティング（重複しない）。

## 例

```
$rig --recipe sage なぜこの API は本番だけ 500 を返す？          # 根本原因の解析と断定
$rig --recipe sage --evolved キャッシュ層は Redis と in-memory どちらが正解？   # 並列演算+予測+提案
$rig --recipe sage このライブラリの v3 は breaking change ある？  # 事実確認（出典つき）
```
