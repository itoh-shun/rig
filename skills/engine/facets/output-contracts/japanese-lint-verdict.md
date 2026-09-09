# output contract: japanese-lint-verdict

reviewer は Markdown fence や前後の説明を付けず、末尾の JSON Schema を満たす単一の
JSON object だけを返します。schema 自体は返しません。キーの省略・追加・重複は禁止です。

各 check の `status` は列挙値から一つだけ選び、`anchor` には報告ファイルまたは差分の短い
根拠箇所を書きます。根拠に、読んだ印象を書いてはいけません。

`sensor_executed` が `FAIL` のときは、ほかの check の値にかかわらず `UNVERIFIED` とします。
`no_errors_left` または `content_preserved` が `FAIL` なら `REVISE` です。
`no_silent_deletion` と `no_unexplained_disabling` が `FAIL` でも `REVISE` です。
`APPROVE` では blocking な check を残さず、`repair_conditions` を `["なし"]` だけにします。
`REVISE` では blocking な check を一つ以上示し、`repair_conditions` に「なし」を含めず、
受け入れられる状態にするための最小の修正条件を一つ以上書きます。

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["report_status", "errors", "warnings", "checks", "repair_conditions", "verdict"],
  "properties": {
    "report_status": {
      "type": "string",
      "enum": ["checked", "unchecked", "not-configured", "missing"]
    },
    "errors": {"type": "integer", "minimum": 0},
    "warnings": {"type": "integer", "minimum": 0},
    "checks": {
      "type": "object",
      "additionalProperties": false,
      "required": ["sensor_executed", "no_errors_left", "content_preserved", "no_silent_deletion", "no_unexplained_disabling", "warnings_read"],
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
        "no_errors_left": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL", "UNKNOWN"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500}
          }
        },
        "content_preserved": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL", "UNKNOWN"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500}
          }
        },
        "no_silent_deletion": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL", "UNKNOWN"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500}
          }
        },
        "no_unexplained_disabling": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL", "N/A"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500}
          }
        },
        "warnings_read": {
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
