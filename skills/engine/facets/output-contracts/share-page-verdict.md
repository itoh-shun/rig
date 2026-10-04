# output contract: share-page-verdict

reviewer は Markdown fence や前後の説明を付けず、末尾の JSON Schema を満たす単一の
JSON object だけを返します。schema 自体は返しません。キーの省略・追加・重複は禁止です。

各 check の `status` は列挙値から一つだけ選び、`anchor` には報告ファイル、ページ、材料の
短い根拠箇所を書きます。根拠に、目で見た印象を書いてはいけません。

`sensor_executed` が `FAIL` のときは、ほかの check の値にかかわらず `UNVERIFIED` とします。
`sensor_clean`・`facts_sourced`・`no_sensitive_content`・`no_impersonation` のいずれかが
`FAIL` なら `REVISE` です。`APPROVE` では blocking な check を残さず、`repair_conditions` を
`["なし"]` だけにします。`REVISE` では blocking な check を一つ以上示し、`repair_conditions`
に「なし」を含めず、公開できる状態にするための最小の修正条件を一つ以上書きます。

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["page", "checks", "repair_conditions", "verdict"],
  "properties": {
    "page": {"type": "string", "minLength": 1, "maxLength": 300},
    "checks": {
      "type": "object",
      "additionalProperties": false,
      "required": ["sensor_executed", "sensor_clean", "facts_sourced", "no_sensitive_content", "no_impersonation", "warnings_addressed"],
      "properties": {
        "sensor_executed": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500}
          }
        },
        "sensor_clean": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL", "UNKNOWN"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500}
          }
        },
        "facts_sourced": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL", "UNKNOWN"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500}
          }
        },
        "no_sensitive_content": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL", "UNKNOWN"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500}
          }
        },
        "no_impersonation": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500}
          }
        },
        "warnings_addressed": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL", "N/A"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500}
          }
        }
      }
    },
    "repair_conditions": {
      "type": "array",
      "minItems": 1,
      "maxItems": 7,
      "items": {"type": "string", "minLength": 1, "maxLength": 500}
    },
    "verdict": {
      "type": "string",
      "enum": ["APPROVE", "REVISE", "UNVERIFIED"]
    }
  }
}
```
