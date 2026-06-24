---
description: rig/pre-mortem — 事前検死。マージ/リリース前に「この変更が本番で壊れた」前提で失敗モードを逆算し、各々に最小ガードレールを対で出す。magi（やるか）の補完で「どう壊れるか」を担当。
argument-hint: [対象の変更/PR/計画（省略可・既定は現在の変更）] [--plan]
---

# rig/pre-mortem — 事前検死 ⚰️

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal・facet 配置順・知識層注入）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。magi と同じ engine を「失敗モードの炙り出し」に使う。

起動後、`--recipe pre-mortem` を既定として次の引数を対象に PARSE する:

```
$ARGUMENTS
```

引数が無ければ現在の作業ツリーの変更（`git diff`）を対象にする。

## やること

対象（diff / PR / 設計案 / 計画）を `pre-mortem` recipe に渡す。手順本体（①対象確定 →②「もう本番で壊れた」前提で失敗モードを逆算 →③ `premortem-report` で構造化）は `facets/instructions/pre-mortem` に従う。

- **時制を未来に置く**: 「壊れるかも」でなく「**もう壊れた。なぜ?**」と断定形で逆算（prospective hindsight＝発見率が上がる実証済み手法）。
- **各失敗モードに最小ガードレールを対で**出す（恐怖の羅列にしない）。技術・運用・データ/セキュリティ・波及の各軸で検死し、可能性×影響でランク。
- **`/rig:magi` との違い**: magi は「やるか（go/no-go）」を裁く。pre-mortem は「**どう壊れるか**」を洗う。magi に諮る前の材料／可決後の最終保険として組み合わせると効く。
- 実作業（読解・検死）は subagent が回す（context-minimal）。ガードレールの実装は `/rig:dev` 等へ委譲。

## flag

- `--plan` … 構成を提示して停止（ドライラン）。

## 例

```
/rig:pre-mortem                       # 現在の変更を事前検死
/rig:pre-mortem この DB 移行をマージする前に
/rig:pre-mortem ./PR の破壊的変更      # 特定対象を検死
```
