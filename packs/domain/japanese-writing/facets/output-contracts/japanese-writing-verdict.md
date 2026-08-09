# output contract: japanese-writing-verdict

独立 reviewer は次の構造だけを返します。完成稿の全面的な書き直しや、根拠のない合格は禁止します。

```text
対象形式: <email|plain-text|markdown|ticket|other>
検査:
- 単一成果物: PASS | FAIL — <完成稿への短いアンカー>
- 形式: PASS | FAIL | UNKNOWN — <指定と完成稿へのアンカー>
- 事実保持: PASS | FAIL | UNKNOWN — <入力と完成稿へのアンカー>
- 推測なし: PASS | FAIL | UNKNOWN — <入力と完成稿へのアンカー>
- 日本語: PASS | FAIL — <文体・敬語・一文の焦点・情報順序・句読点へのアンカー>
- 秘密情報: PASS | FAIL | N/A — <値を引用せず、redaction・要求情報へのアンカー>
- 障害・サポート安全性: PASS | FAIL | N/A | UNKNOWN — <該当箇所へのアンカー>
修正条件:
- <公開可能にするための最小変更。不要なら「なし」>
判定: APPROVE | REVISE | UNVERIFIED
```

blocking な `FAIL` が一つでもあれば `REVISE` とします。入力不足で事実保持または安全性を
確認できなければ推測せず `UNKNOWN` とし、公開可否に関わる場合は `REVISE` とします。
秘密情報の値を根拠欄へ引用してはいけません。値の再表示、秘密情報の要求、redaction の欠落は
blocking な `FAIL` とします。
生成者と同じモデルしか reviewer に使えない場合は、内容に問題が見つからなくても
`UNVERIFIED` とします。
