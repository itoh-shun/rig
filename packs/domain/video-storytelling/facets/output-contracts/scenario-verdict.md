# output contract: scenario-verdict

各レビュアーは次の構造で返す。証拠のない合格や抽象的な差し戻しは禁止する。

```text
観点: <video-language|video-content-safety|engagement|auteur:*>
根拠:
- <ビート/VO/テロップへのアンカー>: <確認した事実または問題>
修正条件:
- <公開可能にするための具体的な最小変更。不要なら「なし」>
判定: APPROVE | APPROVE_WITH_CONDITIONS | REJECT
確信度: high | medium | low
```

根拠のない数値、未出荷機能、重大な権利侵害または誤認が一つでもあれば `REJECT`。情報不足で確認不能なら、推測せず条件付き判定または差し戻しにする。
