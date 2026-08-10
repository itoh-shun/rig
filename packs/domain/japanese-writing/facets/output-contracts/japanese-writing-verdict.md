# output contract: japanese-writing-verdict

独立 reviewer は Markdown fence や前後の説明を付けず、末尾の JSON Schema を満たす単一の
JSON object だけを返します。schema 自体は返しません。キーの省略・追加・重複は禁止です。
完成稿の全面的な書き直しや、根拠のない合格も禁止します。

各 check の `status` は schema の列挙値から一つだけを選び、`anchor` には入力または完成稿の
短い根拠箇所を書きます。秘密情報の値を `anchor` へ引用してはいけません。値の再表示、
秘密情報の要求、redaction の欠落は blocking な `FAIL` とします。

blocking な `FAIL` が一つでもあれば `REVISE` とします。入力不足で事実保持または安全性を
確認できなければ推測せず `UNKNOWN` とし、公開可否に関わる場合は `REVISE` とします。
`APPROVE` では blocking な check を残さず、`repair_conditions` を `["なし"]` だけにします。
`REVISE` では blocking な check を一つ以上示し、`repair_conditions` に「なし」を含めず、
公開可能にする最小の修正条件を一つ以上書きます。生成者と同じモデルしか reviewer に
使えない場合は `UNVERIFIED` とします。

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["target_format", "checks", "repair_conditions", "verdict"],
  "properties": {
    "target_format": {
      "type": "string",
      "enum": ["email", "plain-text", "markdown", "ticket", "other"]
    },
    "checks": {
      "type": "object",
      "additionalProperties": false,
      "required": ["single_artifact", "format", "fact_preservation", "no_inference", "japanese_quality", "secret_handling", "incident_support_safety"],
      "properties": {
        "single_artifact": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500, "pattern": "^(?!\u005cs)[\u005cs\u005cS]*\u005cS$"}
          }
        },
        "format": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL", "UNKNOWN"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500, "pattern": "^(?!\u005cs)[\u005cs\u005cS]*\u005cS$"}
          }
        },
        "fact_preservation": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL", "UNKNOWN"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500, "pattern": "^(?!\u005cs)[\u005cs\u005cS]*\u005cS$"}
          }
        },
        "no_inference": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL", "UNKNOWN"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500, "pattern": "^(?!\u005cs)[\u005cs\u005cS]*\u005cS$"}
          }
        },
        "japanese_quality": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500, "pattern": "^(?!\u005cs)[\u005cs\u005cS]*\u005cS$"}
          }
        },
        "secret_handling": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL", "N/A"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500, "pattern": "^(?!\u005cs)[\u005cs\u005cS]*\u005cS$"}
          }
        },
        "incident_support_safety": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL", "N/A", "UNKNOWN"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500, "pattern": "^(?!\u005cs)[\u005cs\u005cS]*\u005cS$"}
          }
        }
      }
    },
    "repair_conditions": {
      "type": "array",
      "minItems": 1,
      "maxItems": 7,
      "items": {"type": "string", "minLength": 1, "maxLength": 500, "pattern": "^(?!\u005cs)[\u005cs\u005cS]*\u005cS$"}
    },
    "verdict": {
      "type": "string",
      "enum": ["APPROVE", "REVISE", "UNVERIFIED"]
    }
  }
}
```
