# rig manifest（テンプレート）

このファイルをリポジトリの `.claude/rig.md` としてコピーし、プロジェクト固有の値を記入する。
未設定のキーは `汎用既定` 列に示す値がエンジン内で自動適用される。

---

```yaml
# ─────────────────────────────────────────────
# rig プロジェクト manifest
# 配置先: <repo>/.claude/rig.md
# ─────────────────────────────────────────────

# ── ビルド / Lint / テスト コマンド ──────────────────────
# 汎用既定: package.json / gradle / Makefile を自動検出してコマンドを推定する
build: ""           # 例: "./gradlew assembleDebug" / "npm run build"
lint:  ""           # 例: "./gradlew ktlintCheck"   / "npm run lint"
test:  ""           # 例: "./gradlew test"           / "npm test"

# ── ブランチ & CI 戦略 ────────────────────────────────────
# 汎用既定: デフォルトブランチ（main/master）から feature ブランチを切るシンプルフロー
branch:
  base: ""          # 例: "develop"  （空 = リポジトリの default branch）
  naming: ""        # 例: "feature/{issue}-{topic}"  （空 = feature/<topic>）
  ci: ""            # 例: "github-actions"  （空 = push 後の CI 結果を確認するだけ）

# ── レビュアー ────────────────────────────────────────────
# 汎用既定: human / none（Copilot 等のボットを使わず、人間レビュー or レビューなし）
reviewer: ""        # 例: "github-copilot" / "team-lead" / "none"

# ── 本番影響変更 検知パターン ─────────────────────────────
# 汎用既定: security / auth / migration / DI / shared-interface を含むパス・キーワードを
#           汎用ヒューリスティック（ファイル名・変更差分のパターンマッチ）で検出する
production_impact:
  paths: []         # 例: ["app/src/main/java/com/example/auth/", "db/migrations/"]
  keywords: []      # 例: ["SecurityConfig", "@Inject", "interface.*Repository"]

# ── 使用 skill 列挙 ───────────────────────────────────────
# 汎用既定: Claude Code で利用可能な skill を起動時に自動検出して使う
skills: []          # 例: ["grill-with-docs", "tdd", "diagnose", "pr-pre-push-review"]

# ── Knowledge ソースポインタ ──────────────────────────────
# 汎用既定: CONTEXT.md / CLAUDE.md / ADR ディレクトリ等をリポジトリから自動検索する
knowledge:
  context_file: ""  # 例: "docs/CONTEXT.md"
  adr_dir: ""       # 例: "docs/adr/"
  design_docs: []   # 例: ["docs/architecture.md", "docs/api-spec.md"]

# ── デフォルト recipe ─────────────────────────────────────
# bare な `/rig:dev "X"` を実行したときにどの recipe を使うか
# 汎用既定: interactive（毎回ユーザーに recipe を選択させる）
default_recipe: "interactive"  # 例: "review-only" / "full-flow" / "interactive"

# ── デフォルト persona（製品ごとに常時投入する reviewer） ──
# review 時に毎回自動投入する persona 名。`--persona` を毎回打たずに
# この製品のドメイン reviewer を常駐させる。tier 解決(project→user→shipped)で名前解決。
# 汎用既定: [] （自動投入なし＝組み込み reviewer＋--persona 指定分のみ）
default_personas: []  # 例: ["house-authenticity", "mix-engineer"]  （VST 製品）

# ── サイズ判定 閾値 （任意） ──────────────────────────────
# design / review / tdd の size-aware ON/OFF（SKILL §4.4）が参照する行数閾値。
# 汎用既定: pr-hygiene の基準（S≤100 / M≤200 / L≤400 / L超>400）。
# プロジェクトの変更規模感に合わせて調整してよい。
size_thresholds:
  S_max: 100    # この行数以下 → S（軽量）。design/review/tdd 既定 OFF
  M_max: 200    # この行数以下 → M（中規模）。200超 → L（design/review 推奨）
  L_max: 400    # この行数以下 → L（分割検討）。400超 → L超（分割必須）

# ── acceptance-gate 既定 K （任意） ───────────────────────
# gate: acceptance-gate の最大収束試行数 K の全体既定。step の `max_retries` で個別上書き可。
# 汎用既定: 2
default_max_retries: 2

# ── worktree 運用 （任意） ────────────────────────────────
# 汎用既定: worktree を使わず、現作業ブランチのまま進む
worktree:
  enabled: false    # true にすると worktree を使う
  root: ""          # 例: ".worktrees/{issue}-{topic}/"
```

---

## 各キーの補足説明

### build / lint / test
ビルド・静的解析・テスト実行コマンドを文字列で記す。
未設定（空文字列）の場合、エンジンはリポジトリ構造（`package.json` / `build.gradle` / `Makefile` 等）を自動検出して推定コマンドを使う。

### branch
`base` が空の場合はリポジトリのデフォルトブランチ（`git remote show origin` から取得）を使う。
`naming` が空の場合は `feature/<topic>` 形式を使う。

### reviewer
`github-copilot` を指定すると PR 作成後に Copilot Code Review を有効化する。
`none` を指定するとレビューステップを省略する。
未設定は人間レビュー（PR を作成して承認を待つ）扱い。

### production_impact
`paths` と `keywords` のいずれかにマッチする変更があれば「本番影響あり」と判定し、
追加の安全確認を促す。未設定の場合は汎用ヒューリスティック（`auth` / `migration` /
`security` 等を含むパス・差分）で検出する。

### skills
使用を明示したい skill を列挙する。未設定の場合は Claude Code セッション開始時に
利用可能な skill を自動検出し、instruction facet の委譲先候補として用いる。

### knowledge
Knowledge facet が注入するドキュメントの場所を指す。
未設定の場合はリポジトリを検索して `CONTEXT.md` / `CLAUDE.md` / `docs/` を探す。

### default_recipe
裸の `/rig:dev "X"` 実行時に使う recipe 名。
`interactive`（または未設定）の場合は毎回ユーザーに選択を求める。
recipe の詳細スキーマは `SKILL.md §3.5` を参照。

### default_personas
この製品の review/adversarial step に毎回自動投入する reviewer persona 名のリスト。
各名は tier 解決（project → user → shipped）で `/rig:persona` 生成 persona 等を名前解決する。
解決した persona が `inject: [[slug]]` を宣言していれば wiki も同伴注入される。
最終 reviewer は「組み込み reviewer ＋ recipe `personas[]` ＋ default_personas ＋ `--persona`」の
名前和集合（dedup）。この run だけ外すには `--no-default-personas`。
未設定（`[]`）は自動投入なし。詳細は `SKILL.md §5「manifest default_personas の自動投入」`。

### size_thresholds
`SKILL.md §4.4` の size-aware 既定（design / review / tdd の自動 ON/OFF）が参照する行数閾値。
サブキーは `S_max` / `M_max` / `L_max`。変更行数が `S_max` 以下＝S、`M_max` 以下＝M（S/M は重い step を既定 OFF）、
`M_max` 超＝L 以上（design / review を推奨）、`L_max` 超＝L超（分割必須）。
未設定の場合は pr-hygiene のベースライン（S≤100 / M≤200 / L≤400 / L超>400）を使う。

### default_max_retries
`gate: acceptance-gate` の最大収束試行数 K の全体既定（未設定時 2）。
step 個別には `SKILL.md §3.5` の `max_retries` キーで上書きする。
§6 stuck-guard（同一エラー反復のカウンタ）とは独立。

### worktree
`enabled: true` にすると、`root` パターンに従って git worktree を作成してからフローを開始する。
`{issue}` / `{topic}` はエンジンが実行時に置換する。

---

> このスキーマは最小完全形（MINIMAL but complete）である。
> プロジェクト固有の追加キーは自由に末尾へ追記してよい。
> manifest に存在しないキーには常に汎用既定が適用される。
