# output contract: pack-author-report

reviewer は Markdown fence や前後の説明を付けず、末尾の JSON Schema を満たす単一の
JSON object だけを返します。schema 自体は返しません。キーの省略・追加・重複は禁止です。

`declared` が `FAIL`（`pack sync` / `validate` / `doctor` のどれかが走っていない、または
失敗した）なら、ほかの check にかかわらず `UNVERIFIED` です。`sources_only` か
`case_is_draft` が `FAIL` なら `REVISE` です。`measured` は `PASS`（計測された）、
`STRUCTURAL_ONLY`（provider が無く未計測）、`FAIL`（走って失敗）のいずれかで、
`STRUCTURAL_ONLY` は `APPROVE` を妨げませんが `measured_note` に必ずその旨を書きます。
`APPROVE` は「人に承認を求めてよい draft である」という意味で、pack が承認された
という意味ではありません。

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["pack_id", "checks", "ask_a_person", "next_commands", "repair_conditions", "verdict"],
  "properties": {
    "pack_id": {"type": "string", "minLength": 1, "maxLength": 100},
    "checks": {
      "type": "object",
      "additionalProperties": false,
      "required": ["declared", "sources_only", "case_is_draft", "measured", "measured_note"],
      "properties": {
        "declared": {"type": "object", "additionalProperties": false, "required": ["status", "anchor"],
                     "properties": {"status": {"type": "string", "enum": ["PASS", "FAIL"]},
                                    "anchor": {"type": "string", "minLength": 1, "maxLength": 500}}},
        "sources_only": {"type": "object", "additionalProperties": false, "required": ["status", "anchor"],
                         "properties": {"status": {"type": "string", "enum": ["PASS", "FAIL"]},
                                        "anchor": {"type": "string", "minLength": 1, "maxLength": 500}}},
        "case_is_draft": {"type": "object", "additionalProperties": false, "required": ["status", "anchor"],
                          "properties": {"status": {"type": "string", "enum": ["PASS", "FAIL"]},
                                         "anchor": {"type": "string", "minLength": 1, "maxLength": 500}}},
        "measured": {"type": "string", "enum": ["PASS", "STRUCTURAL_ONLY", "FAIL"]},
        "measured_note": {"type": "string", "minLength": 1, "maxLength": 500}
      }
    },
    "ask_a_person": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 300}, "maxItems": 20},
    "next_commands": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 300}, "maxItems": 5},
    "repair_conditions": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 300}, "minItems": 1, "maxItems": 10},
    "verdict": {"type": "string", "enum": ["APPROVE", "REVISE", "UNVERIFIED"]}
  }
}
```
