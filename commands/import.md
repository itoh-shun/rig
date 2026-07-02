---
description: "rig/import — ネット上の外部 skill（GitHub の SKILL.md / plugin）を解析して rig ブリックへ翻訳し、出所とハッシュを skills-lock.json に記録する取り込み機構。--check-updates で上流差分検知。/rig:forge（自作）の対＝既にあるものを取り込む。"
argument-hint: "[\"<GitHub URL | owner/repo | ローカルパス>\" | --discover \"<欲しい能力>\"] [--path <repo内パス>] [--all] [--name <slug>] [--user] [--dry-run] [--check-updates]"
---

# rig/import — 外部 skill の取り込み 📥

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・§2 ブリック目録・§8 Native-first・context-minimal）に従うこと。** このコマンドは入口であり、手順本体は `facets/instructions/skill-import` にある（重複定義しない）。

起動後、`facets/instructions/skill-import` に従って外部 skill を取り込む:

```
$ARGUMENTS
```

## やること

「ネットにある skills を真似しながら包括する」を機構にする。外部 skill を**委譲（最優先）→ 翻訳 → 知識のみ**の順で取り込み方を判断し、生成は既存ジェネレータ（`/rig:forge` `/rig:persona` `/rig:knowledge`）へ委譲、**出所と SHA-256 を `skills-lock.json` に記録**して再現可能・更新検知可能にする。

- **`--discover "<欲しい能力>"`**：ソースを知らなくても探せる。GitHub 横断検索→適合度/ライセンス/保守性/重複でランク→短リスト提示。見つからなければ `/rig:persona`/`/rig:forge` の自作へ＝**探す→無ければ作る**。
- **委譲**：そのまま動く skill は移植しない（薄い routing ブリックだけ作る）。
- **翻訳**：判断・観点・手順を pack の定石（persona/knowledge/instruction/recipe/output-contract/command）に分解。
- **`--check-updates`**：lock の全エントリを上流と照合し、更新あり/最新/取得不能を一覧。再取り込みは提案まで（自動追従しない）。
- **import-gate（試用）**：lock 記録の前に生成ブリックを実地試験（persona はサンプル diff で契約遵守を、recipe は `plan --json`+validate を）。「取り込んだ」でなく「取り込んで動いた」。
- **方言も食べる**：`.cursorrules`・`AGENTS.md`・他 repo の `CLAUDE.md`・MCP ツール定義・プロンプト集も取り込み対象（規範→policy／観点→persona/knowledge に翻訳）。
- **書き込みは確認必須・冪等**。ライセンス不明なら本文を持ち込まず委譲のみ。

## 例

```
/rig:import --discover "DBマイグレーションに強いレビュー観点"          # ネットから探す→ランク→取り込み
/rig:import anthropics/skills --path skills/frontend-design/SKILL.md   # 特定 skill を取り込む
/rig:import https://github.com/obra/superpowers --dry-run              # 走査して候補提示・書き込みなし
/rig:import ~/.claude/skills --all --dry-run                           # 手元のスキル集の判断サマリを一覧（書き込みなし）
/rig:import ~/.claude/skills --all                                     # 候補全件を一括取り込み（承認は一括1回・lock 一括記録）
/rig:import owner/repo --name tanka-review --user                      # user 層に取り込む
/rig:import --check-updates                                            # 取り込み済み全 skill の上流差分検知
```

取り込んだ brick は `--list` / `/rig:catalog` に出る。出所は `skills-lock.json` が持ち続ける。
