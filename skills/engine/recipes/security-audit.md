---
name: security-audit
description: コンポーネントを攻撃者視点で能動的に監査する read-only recipe。threat-model→（決定論センサー）→exploit 探索の順で、今あるコードの刺さる経路を PoC 付きで洗い出す。diff レビューでなく「既存コードのどこが突けるか」を測る。
scope: shipped
steps:
  - id: threat-model
    instruction: security-audit
    pattern: serial
    personas: [security/threat-modeler]
  - id: exploit-research
    instruction: security-audit
    pattern: parallel-fanout
    gate: review-gate
    personas: [security/exploit-researcher, security-reviewer]
    output_contract: security-findings
    acceptance:
      - "各 Confirmed 所見に 攻撃シナリオ(1行)・最小PoC・file:line・root cause・canonical な修正案 が揃っている"
      - "未確認は Suspected(情報不足) として分離され、低確信の Critical/High が無い"
      - "監査スコープ(自プロダクト/許可済み環境)が宣言され、外部への攻撃トラフィックを送っていない"
autonomy: interactive
---

# security-audit

> **セキュリティ pack 注記**: rig engine（`SKILL.md`）を dev/sales/magi 等と**共用**する security（ホワイトハッカー）pack の recipe。engine は書き換えず、`security/*` persona・`security-audit` instruction・`security-findings` output-contract・`attack-catalog` knowledge を足すだけで成立する。`/rig:sec audit` から起動。

## 使う場面

**diff でなく「今あるコード（コンポーネント/機能）」を能動的に攻めて脆弱性を洗い出したい**時。`review-only`（変更 diff の3-way レビュー）や `security_review`（single-diff の security 評価）が「入ってくる変更が安全か（守り）」を見るのに対し、これは**攻撃者視点で既存資産のどこが実際に突けるか**を測る。

- 「この認証周りのモジュール、外部から突けるところある？」
- 「決済ハンドラを攻撃者目線で監査して」
- 「依存に既知 CVE が無いか＋コードの攻撃面を棚卸しして」

## 展開

1. **スコープ宣言** — 対象パス/機能と「守る資産」を1行。**倫理境界**（自プロダクト or 許可済みローカル/ステージング・DAST は範囲外）を確認。
2. **threat-model**（`security/threat-modeler`）— 信頼境界とデータフローを地図化し STRIDE で優先脅威を絞る。
3. **決定論センサー（任意）** — プロジェクトに SAST/SCA/secret スキャンがあれば `scripts/sast_adapter.py`・`workbench.py scan-secrets` の出力を取り込み、機械が拾える面を先に潰す。
4. **exploit 探索**（`security/exploit-researcher` ＋独立検証 `security-reviewer` を `parallel-fanout`）— `attack-catalog` の技法で刺さる経路を試す。**刺さったものだけ Confirmed**。
5. **集約**（`review-gate`）— `security-findings` 形式で severity 順・証拠アンカー付きに提示。

## 出口

read-only。修正まで進めるなら `pentest-fix` へ（各 Confirmed を gated 実装＋re-exploit で塞ぐ）。一時ファイルは破棄し本物のコード・履歴を汚さない。

## 差別化（rig を通す価値）

素の AI に「脆弱性を探して」と頼むと、**印象論の羅列**（刺さるか未検証・重大度が根拠なし・弥縫策を提案）が返りやすい。この recipe は `security-findings` 契約で **PoC が刺さった Confirmed と未確認 Suspected を分離**し、低確信の重大判定を禁止し、修正案を canonical に縛る。gate と独立検証者が「それ本当に刺さる？」を機械的・構造的に問う。
