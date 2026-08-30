# output contract: document-review-verdict

独立 reviewer は Markdown fence や前後の説明を付けず、末尾の JSON Schema を満たす単一の
JSON object だけを返します。schema 自体は返しません。キーの省略・追加・重複は禁止です。
資料の書き直しや、根拠のない合格も禁止します。

`lens` には自分の観点を一つだけ書きます。他の観点の指摘は自分の verdict に含めません。
一つの資料を複数の観点で見るときは reviewer を分けて走らせ、それぞれが自分の verdict を
返します。

`declared_reader` には資料が宣言している読み手をそのまま書き、宣言が無ければ `未宣言`
とします。読み手が未宣言のまま構成・根拠・適合を断定してはいけません。

各 `checks[]` の `anchor` には資料または添付材料の短い根拠箇所を書きます。秘密情報の値を
`anchor` へ引用してはいけません。`status` は自分が実際に確認できたものだけを `PASS` とし、
材料が足りずに確認できなかったものは `UNKNOWN` とします。`UNKNOWN` は失敗ではなく、
測っていないという記録です。推測で `PASS` を埋めてはいけません。

`FAIL` が一つでもあれば `REVISE` とします。`UNKNOWN` だけが残り、それが資料の目的に
関わる場合も `REVISE` とします。`APPROVE` では `FAIL` を残さず、`repair_conditions` を
`["なし"]` だけにします。`REVISE` では `repair_conditions` に「なし」を含めず、
公開可能にする最小の修正条件を書きます。生成者と同じモデルしか reviewer に使えない場合は
`UNVERIFIED` とします。

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["lens", "declared_reader", "checks", "repair_conditions", "verdict"],
  "properties": {
    "lens": {"type": "string", "enum": ["structure", "evidence", "audience"]},
    "declared_reader": {"type": "string", "minLength": 1, "maxLength": 200},
    "checks": {
      "type": "array",
      "minItems": 1,
      "maxItems": 12,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["id", "status", "anchor"],
        "properties": {
          "id": {"type": "string", "minLength": 1, "maxLength": 80},
          "status": {"type": "string", "enum": ["PASS", "FAIL", "UNKNOWN"]},
          "anchor": {"type": "string", "minLength": 1, "maxLength": 500}
        }
      }
    },
    "repair_conditions": {
      "type": "array",
      "minItems": 1,
      "maxItems": 7,
      "items": {"type": "string", "minLength": 1, "maxLength": 500}
    },
    "verdict": {"type": "string", "enum": ["APPROVE", "REVISE", "UNVERIFIED"]}
  }
}
```
