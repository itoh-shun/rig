# policy: org-policy

組織ポリシーが効いているリポジトリ（`.rig/org.json` が存在する）で、**末尾注入されるガードレール**。ポリシーが無いリポジトリでは何も要求しない（既定で不活性＝個人開発者はこの規律を負わない）。

## 禁止事項

- **ポリシー層を緩める変更の提出禁止。** team / project 層で、上位層が要求する criterion の削除、`quorum` の引き下げ、`separation_of_duties` の解除、`expires_hours` / `max_days` の延長、`required_for_force` / `audit.chain_required` の無効化、role への権限追加を**提案も実装もしない**。必要なら**上位層の改定として明示的に提案**する（`rig-wb govern policy lint` が層とフィールドを名指して落とす）。
- **`sealed_roles` への自己登録禁止。** 下位層の `members` で封印ロール（`quality-owner` 等）を自分や他人に割り当てない。
- **監査台帳の編集禁止。** `.rig/ledger.jsonl` を書き換え・削除・並べ替えしない。連鎖が壊れると `rig-wb govern audit verify` が検出し、conformance が FAIL になる（そして原因調査は必ず人間の仕事になる）。
- **承認の自己付与禁止。** 自分が著者の task に自分で承認を付けない（職務分離により数えられないが、記録は残る）。
- **`--force` の無記名使用禁止。** ポリシーが `required_for_force` を要求している場合、例外（waiver）を先に取る。理由と期限のない override を通そうとしない。
- **ガバナンス設定ファイルを task diff に混ぜない。** `.rig/org.json` / `.rig/policy/*.json` / `.rig/waivers.json` の変更は、機能変更と同じ PR に含めない（1 PR 1 関心事。ゲートを緩める変更が機能変更に紛れるのが最悪の混在）。

## 必須事項

- **accept 前に権限と承認を確認する。** `rig-wb govern whoami` で自分の権限、`rig-wb govern approve status <task-id>` で承認状況を読んでから accept に進む（「通らなかったので --force」への短絡を防ぐ）。
- **例外には理由・対象・期限を書く。** waiver の `--reason` は3か月後に読み返して意味が通る具体性で書く（追跡 Issue 番号を含める）。期限を延長し続けている例外は、ポリシー改定として提案する。
- **ポリシー変更は `policy lint` を通してから提出する。** `rig-wb govern policy lint` が exit 0 であることを確認する（層の整合は目視では追えない）。

## 推奨事項

- 共通ポリシーは**1つの共有チェックアウト**を `$RIG_POLICY_HOME` 経由で参照する（コピーを配るとドリフトする）。
- org 層は薄く保つ。全社に効く基準だけを置き、チーム固有は team 層へ（org 層が厚いと team が例外を出し続ける）。
- 権限は**役割単位で最小に**配る。全権限を1人に集中させると、その人が不在の日にフローが止まり `--force` が常用される。
