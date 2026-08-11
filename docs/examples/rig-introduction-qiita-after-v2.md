# AI を使った開発を、基準を満たすまで回すワークベンチ Rig

AI の実行は毎回結果が変わります。AI を使った開発を支援するワークベンチが Rig です。AI とチャットしてコードを書かせる、という使い方もできます。ただ Rig なら、それを開発フローとして組み立てられます。そこが違います。

## 一番大事なのは acceptance-gate

受け入れ基準のリストを持つのが acceptance-gate です。基準を満たすまで、最大 K 回まで繰り返して収束させます。K 回でも満たせなければ、人間にエスカレーションされます。

基準は recipe に acceptance として書きます。build が成功する、lint が0件、レビューで REJECT がない。そういうものです。

毎回の経路は変わりますが、品質は同じところに収束します。これを determinism-by-gate と呼びます。

## 起動してから走り出すまでに4段

処理は PARSE、RESOLVE、COMPOSE、RUN の順に進みます。PARSE で起動文字列をフラグと自由記述に分解し、RESOLVE では manifest、recipe、フラグ、サイズ既定の順で設定が確定します。後の段が前の段を上書きします。COMPOSE が作るのはサブエージェント用のプロンプトで、step ごとに persona、instruction、policy を組み合わせます。RUN が実行です。

固定のワークフローを流すわけではありません。毎回組み立てます。

## recipe は step の束

step は工程です。step に指定できるのは instruction、pattern、persona、policy、output_contract などです。

recipe が探されるのは project、user、shipped の3階層で、先に見つかったほうが優先されます。プロジェクト固有の recipe があれば、同じ名前のものよりそちらが使われます。継承は extends でできます。

## 親は diff を読まない

実装もレビューも調査も、実作業は必ずサブエージェントに投げます。親に残るのは dispatch と集約とゲート判断だけです。長い diff やログを親が読み込むと、コンテキストが汚れるからです。

サブエージェントからの返しは output contract で形を決めておいて、親は判定行だけ見ます。並列にできる観点は、1メッセージでまとめて dispatch します。たとえばレビューは、security、design、test の3つの観点を並列で回すのが基本です。

## 200行を超えるまでは軽く回す

重い工程は、変更の規模で自動的にオフになります。既定は200行で、それ以下なら design、review、tdd はオフ、超えれば推奨に変わります。閾値は manifest で変えられます。

## 動かす

渡すのは自然文です。

```sh
rig-wb run bugfix
```

先に何をするか見たいときは plan です。構成を見せたところで止まります。

```sh
rig-wb plan bugfix --json
```

Claude Code からなら `/rig:go` で同じことができます。step を一部だけ動かすなら `--only`、`--from`、`--to`、`--skip` があります。

実装は隔離された worktree で走ります。accept か discard は、結果を見てから選べます。AI が間違えることもあるので、最後は人間が確認したほうがいいです。同時に複数のタスクを走らせているときは、board で全部の状態を見られます。
