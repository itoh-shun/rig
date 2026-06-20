# policy: branch-strategy

## facet: policy / branch-strategy

ブランチの作成・命名・ベース選択に関するポリシーです。プロンプト末尾への注入を前提とします。

### 優先順位

1. **manifest 優先** — プロジェクトの manifest にブランチ命名規則・base ブランチ・フローが定義されている場合は、それに従う。
2. **フォールバック** — manifest に記載がない場合は以下のデフォルトを適用する。

### デフォルトルール（manifest 未設定時）

- base ブランチ: リポジトリのデフォルトブランチ（例: `main`）から切る。
- ブランチ名: `feature/<短い説明>` 形式を使用する（例: `feature/add-auth-endpoint`）。
- 1 ブランチ = 1 関心事（implementer ペルソナと同じ原則）。

### 禁止事項

- 誤った base ブランチから切らない（特に release ブランチや他の feature ブランチからのマージを避ける）。
- manifest に反する命名・フローを使わない。
- 1 つのブランチに複数の無関係な変更を混在させない。
