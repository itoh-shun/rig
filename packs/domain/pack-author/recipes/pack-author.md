---
name: pack-author
description: 手元の材料から pack を起草し、道具に宣言と検査をさせ、承認は人に残す opt-in recipe（#547 slice 1）。URL は取りに行かない。
scope: shipped
autonomy: interactive
steps:
  - id: intake
    instruction: pack-intake
    pattern: serial
    personas: [pack-author]
    policies: [pack-author-rules]
  - id: draft
    instruction: pack-draft
    pattern: serial
    personas: [pack-author]
    policies: [pack-author-rules]
  - id: declare
    instruction: pack-present
    executor: checks-only
    pattern: serial
    max_retries: 1
    checks:
      - "test -d \"${RIG_PACK_DIR:?set RIG_PACK_DIR to the drafted pack directory}\""
      - "rig-wb pack sync \"$RIG_PACK_DIR\""
      - "rig-wb pack validate \"$RIG_PACK_DIR\""
      - "rig-wb pack doctor \"$RIG_PACK_DIR\""
      - "rig-wb pack test \"$RIG_PACK_DIR\" --json > \"$RIG_PACK_DIR/.pack-test.json\" || true"
  - id: present
    instruction: pack-present
    pattern: serial
    gate: acceptance-gate
    max_retries: 1
    acceptance:
      - "pack sync / validate / doctor が実際に走り、その出力が残っている"
      - "起草した asset の全てが材料の一覧にある file を出典としている"
      - "evaluation case が draft のままで、approved にされていない"
      - "pack test の結果が言い換えずに報告されている（structural_only は未計測と書かれている）"
      - "材料から埋まらない欄が人に決めてもらう欄として空のまま渡されている"
    personas: [pack-draft-reviewer]
    policies: [pack-author-rules, independent-verification]
    output_contract: pack-author-report
---

# pack-author

手元の材料（file）を渡すと、pack の asset を起草し、`pack sync` / `validate` / `doctor` /
`test` を走らせ、**人が承認するのに要るものだけを並べて止まる** recipe です。
#547 の slice 1、材料は手元のものだけ、URL は取りに行きません。

## 構成

1. `intake` — 材料を path と sha256 の一覧に固定します。URL と存在しない path は
   取得も推測もせず、取得しない旨を返します。
2. `draft` — `pack-author` が雛形を作り、asset を材料から起草し、各 asset に出典を残し、
   evaluation case を `draft` で書きます。
3. `declare` — provider を呼ばない checks。`RIG_PACK_DIR` の pack に対して
   `pack sync` → `validate` → `doctor` → `test` を走らせ、出力を残します。`test` は
   provider が無ければ `structural_only` を返し、それは失敗ではなく未計測です。
4. `present` — 書き手とは別の `pack-draft-reviewer` が、材料・asset・検査の出力を
   突き合わせ、`pack-author-report` を gate の内側へ返します。

## 立ち位置

**rig が起草し、道具が宣言と検査をし、人が承認する。** 自動承認ではありません——
生成した pack を `validate` が黙って通すなら gate に意味が無くなります。起草だけでも
ありません——空の雛形を承認しろと言われるより、計測済み（または未計測と明記された）
結果を承認するほうが楽だからです。gate 自体は変えません。`eval promote` は人が打ちます。

## この pack が `tool` である理由

`checks:` を宣言した recipe を積めるのは `type: tool` だけです（`RECIPE_CHECKS_TYPES`）。
`declare` step がホスト上で `rig-wb pack ...` を実行するので、その表示です。実行されるのは
上に書いた 5 行で、対象は `RIG_PACK_DIR` が指すディレクトリだけです。

## この pack 自身の evaluation case について

同梱の 2 case は `approved` で出荷しています。`validate` が、prompt を持つ pack に
approved な case を要求するためです（layout-gate と同じ出し方）。**書かれた case で
あって、計測された case ではありません。** この環境には Claude 以外の provider が無く、
`pack test` は `structural_only` を返します。この recipe が起草する pack の case を
`draft` に留める規則は、この pack 自身には遡って適用していません。

## 未着手のこと（#547 の slice 2 と 3）

- 材料の出所（URL・取得日時・licence）を manifest に残す欄はまだありません。
- URL を取りに行く機能はありません。取りに行くなら、その決定は SSRF の面を含めて
  別途下されます。
