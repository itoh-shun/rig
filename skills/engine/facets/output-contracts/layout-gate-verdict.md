# output contract: layout-gate-verdict

reviewer は Markdown fence や前後の説明を付けず、末尾の JSON Schema を満たす単一の
JSON object だけを返します。schema 自体は返しません。キーの省略・追加・重複は禁止です。

各 check の `status` は列挙値から一つだけ選び、`anchor` には検査の出力または差分の短い
根拠箇所を書きます。根拠に、目で見た印象を書いてはいけません。

`gate_executed` が `FAIL` のときは、ほかの check の値にかかわらず `UNVERIFIED` とします。
溢れ・重なり・切れのいずれかが `FAIL` なら `REVISE` です。`APPROVE` では blocking な check を
残さず、`repair_conditions` を `["なし"]` だけにします。`REVISE` では blocking な check を
一つ以上示し、`repair_conditions` に「なし」を含めず、出荷できる状態にするための最小の
修正条件を一つ以上書きます。

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["artifact_kind", "checks", "repair_conditions", "verdict"],
  "properties": {
    "artifact_kind": {
      "type": "string",
      "enum": ["slides", "html-pages", "html-flow", "cards", "other"]
    },
    "checks": {
      "type": "object",
      "additionalProperties": false,
      "required": ["gate_executed", "overflow", "collision", "clipping", "no_silent_content_loss", "no_threshold_relaxation"],
      "properties": {
        "gate_executed": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500}
          }
        },
        "overflow": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL", "UNKNOWN"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500}
          }
        },
        "collision": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL", "UNKNOWN"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500}
          }
        },
        "clipping": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL", "N/A", "UNKNOWN"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500}
          }
        },
        "no_silent_content_loss": {
          "type": "object",
          "additionalProperties": false,
          "required": ["status", "anchor"],
          "properties": {
            "status": {"type": "string", "enum": ["PASS", "FAIL", "UNKNOWN"]},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 500}
          }
        },
        "no_threshold_relaxation": {
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
