# policy: independent-verification

## facet: policy / independent-verification

検証（受け入れ照合・採点・レビュー判定）の**担い手**に関するポリシー。プロンプト末尾への注入を前提とする。

> 原則：**採点者は生成者と別人**。成果物を作った agent に、その成果物の合否を判定させない。

### なぜ

エージェントに自分の出力を採点させると、**甘く付ける**（self-grading bias / self-praise）。生成直後は自分の意図が頭に残っていて、行間を補完して読んでしまうからだ。読者（次の gate）にはその文脈が無い。実例：de-ai-smell で自分の記事を自己採点して 41/50「合格」と付けたが、別の目に見せたら一発で「AI 臭い」＝23/50 相当だった。18点の過大評価は、生成者が採点したことが原因。

### ルール

1. **gate の照合は、生成した step とは別の subagent に dispatch する**。親（オーケストレータ）は照合を生成者にやらせず、独立 reviewer/checker を立て、その verdict 行だけを読む（context-minimal と同経路）。
2. **同一ターンで「生成 → 自分で合格判定」する経路を禁止**。acceptance-gate / review-gate / goal-loop の照合 / de-ai-smell の採点・scenario の検閲は、すべて生成と別の担い手で行う。
3. **完全な独立が取れない小規模の場合**は、最低限の近似として「**コンテキストを変える**」— 再アンカー後に別ロールとして読む／音読する／一拍置く。ただし「生成直後に同一ロールのまま自己合格」は近似としても不可。
4. **疑わしきは不合格側に倒す**（生成者バイアスの逆張り）。独立検証者は「通す理由」でなく「落とす理由」を探す姿勢で読む。

### 適用範囲

- `patterns/acceptance-gate`・`patterns/review-gate`：照合・レビューは生成 step と別 persona/subagent。
- `recipes/goal-loop`：⑤照合（verification）を生成（④委譲先）と切り離す。
- `recipes/de-ai-smell`・`recipes/scenario`：採点・検閲は書き手と別。**書いた本人の自己スコアを最終判定にしない**。

### 禁止事項

- 生成者に自分の成果物の最終合否を出させない。
- 自己採点の高得点を「合格」の根拠に使わない（参考値に留める）。
