---
# rig 自身のリポジトリの manifest。正本のスキーマは skills/engine/manifests/_template.md。
# ここには汎用既定から実際に変えるキーだけを書く（テンプレを丸写しして空欄を並べると、
# 設定していないのか空文字を設定したのかが読めなくなる）。

# ── コスト予算 ────────────────────────────────────────────
# rig は fan-out を絞る手段（--budget / default_budget）を持ちながら、このリポジトリ自身では
# 一度も設定していなかった（#532）。既定は「制限なし」なので、重い操作ほど明示指定が要る一方、
# 既定側に歯止めが無い状態だった。
#
# `mid` を選ぶ理由: `low` は workflow バックエンドを禁止する。rig は自分自身で /rig:drill と
# workflow を回すリポジトリなので、`low` を既定にすると自分の運用と正面から衝突し、使うたびに
# `--budget mid` を打つことになる。毎回外される既定は歯止めではない。
# `mid`（組み込み 3-way ＋ 選択投入2枠）を床にして、絞りたい場面で `--budget low` を明示する。
default_budget: mid
---

# rig プロジェクト manifest

上の frontmatter が実体。`parse_frontmatter` は**ファイル冒頭の `---` ブロックだけ**を読むので、
散文はすべてこの下に置く（コードブロックに YAML を書いても読まれない）。

`.gitignore` は `.claude/` 配下をローカル設定として無視するが、このファイルだけは
`!.claude/rig.md` で追跡対象に戻している。manifest はプロジェクトの宣言であって個人の設定では
ないので、コミットされていなければ誰の環境でも効かない。

## 有効にするには consent が要る

置いただけでは効かない。エンジンは未同意の manifest を**解析せず**、1行警告して「manifest が
無い」ものとして振る舞う（`ensure_manifest_trusted`）：

```
[WARN] untrusted project manifest ignored: <repo>/.claude/rig.md
       (consent: --allow-project-manifest or RIG_ALLOW_PROJECT_MANIFEST=1)
```

これは弱点ではなく設計。リポジトリを clone しただけで他人の manifest が自分の環境の既定を
書き換えたら、それは設定ではなく実行だ。同意は**ファイルのハッシュとして記録**されるので、
一度通せば内容が変わるまで黙って通り、編集すれば再同意を求められる。

```console
RIG_ALLOW_PROJECT_MANIFEST=1 rig-wb run <recipe>   # または --allow-project-manifest
```

したがって `default_budget: mid` は、**各自が一度 consent した環境でだけ**効く。

## 支出の計測について（#532）

`default_budget` が絞るのは**投入**であって、支出そのものではない。支出が計測されるのは
provider が構造化された usage を返すときだけで、`claude` / `codex` を CLI として使う構成では
`rig-wb runs --cost` は「unmeasured」と答え続ける。それは欠陥ではなく、**推定値を計測値の
ふりをさせないための設計**（未計測を 0 として描かない）。

計測された支出が見たい場合は、verifier を `claude` CLI ではなく `anthropic` HTTP provider に
向ける：

```console
rig-wb run <recipe> --provider claude --verifier-provider anthropic
```

HTTP provider（`anthropic` / `ollama` / `lmstudio`）は usage フィールドから自動的に計測される。
CLI provider の実支出は Anthropic の Usage & Cost Admin API 側で見る。
