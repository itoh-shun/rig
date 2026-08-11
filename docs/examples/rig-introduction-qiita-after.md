# Rig — AI を使った開発をフローとして組み立てるワークベンチ

Rig は AI を使った開発を支援するワークベンチです。AI とチャットしてコードを書かせることもできますが、Rig を使うとそれを開発フローとして組み立てられます。違いはそこです。

## 起動から実行までの4段

Rig の動きは PARSE、RESOLVE、COMPOSE、RUN の4段に分かれます。

- **PARSE**: 起動文字列をフラグと自由記述に分解
- **RESOLVE**: manifest、recipe、フラグ、サイズ既定の順に設定を確定。後の段が前の段を上書き
- **COMPOSE**: step ごとに persona、instruction、policy を組み合わせ、サブエージェント用のプロンプトを作成
- **RUN**: 実行

固定のワークフローを流すのではなく、毎回組み立てる設計です。

## recipe と step

recipe は step の束で、step は工程です。step には instruction、pattern、persona、policy、output_contract などを指定できます。

recipe は project、user、shipped の3階層から探され、先に見つかったものが優先されます。プロジェクト固有の recipe を置けば、同じ名前のものより優先されます。extends を使えば継承もできます。

## acceptance-gate

AI の実行は毎回結果が変わります。acceptance-gate は受け入れ基準のリストを持ち、その基準を満たすまで最大 K 回繰り返して収束させます。K 回で満たせなければ人間にエスカレーションされます。

基準は、build が成功する、lint が0件、レビューで REJECT がない、といったものです。recipe に acceptance として書きます。

経路は毎回変わっても品質は同じところに収束する——この考え方が determinism-by-gate です。

## context-minimal

実装、レビュー、調査といった実作業は必ずサブエージェントに投げます。親がやるのは dispatch と集約とゲート判断だけです。親が長い diff やログを読み込むとコンテキストが汚れるからです。

サブエージェントには output contract で決めたフォーマットで返させ、親は判定行だけを読みます。並列にできる観点は1メッセージで複数 dispatch します。レビューは security、design、test の3観点を並列で回すのが基本です。

## 変更の規模で重い工程を落とす

変更の規模によって、重い工程は自動でオフになります。既定では200行以下で design、review、tdd がオフになり、それを超えると推奨されるようになります。閾値は manifest で変えられます。

## 使い方

やりたいことは自然文で渡します。

```sh
rig-wb run bugfix
```

実行前に何をするか見たいときは plan を使います。構成を見せて止まります。

```sh
rig-wb plan bugfix --json
```

Claude Code からは `/rig:go` で同じことができます。step の一部だけ実行したいときは `--only`、`--from`、`--to`、`--skip` が使えます。

実装は隔離された worktree で行われるので、結果を見てから accept か discard を選べます。複数のタスクを同時に走らせているときは board で全部の状態を見られます。

## 最後は人間が確認する

AI は間違えることもあります。最後は人間が確認したほうがいいです。
