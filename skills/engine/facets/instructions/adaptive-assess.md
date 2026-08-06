# instruction: adaptive-assess

`adaptive-bugfix`専用の決定論的risk評価step。LLMへ依頼せず、現在のdiffを
`rig_workbench.orchestrate.adaptive.analyze_diff`で分類し、変更domain、risk
signal、primary reviewer、high-risk時のsecondary reviewerをrun stateへ記録する。

解析不能・diff取得失敗・不正な結果はfail-openにせずsafe stopとする。このstep
自体はprovider invocationへ数えない。後続の`targeted-review`は記録された
reviewerだけを起動し、recipeの呼び出し上限を超えてはならない。
