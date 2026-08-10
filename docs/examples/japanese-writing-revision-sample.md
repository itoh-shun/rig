# Qiita風記事の訂正サンプル

同じ技術内容を保ったまま、読み手が手順と理由を追いやすい記事へ整える例です。
実在サービスの仕様や第三者の記事を転載したものではありません。

## 訂正前：下書き

# Docker Composeで開発環境を作ったら最初にやること

Docker Composeは複数のコンテナをまとめて起動できるので便利です。今回はNode.jsとPostgreSQLを使う例ですが、まずDockerを入れてからファイルを作って、コマンドを実行します。環境によっては動かないことがあるので、そのときはログを確認してください。

```yaml
services:
  app:
    image: node:22
    ports:
      - "3000:3000"
  db:
    image: postgres:16
```

これで起動できます。データベースは消えると困るので、実際に使う場合はボリュームを設定したほうがいいと思います。あと、環境変数も必要になる場合があります。

## 訂正後：公開用の記事

# Docker ComposeでNode.jsとPostgreSQLの開発環境を作る

Docker Composeを使うと、Node.jsのアプリケーションとPostgreSQLを1つの設定ファイルから起動できます。ここでは、最小構成を作り、起動確認までを行います。

## 前提

- Docker DesktopまたはDocker Engineがインストール済み
- `docker compose` コマンドが利用できる

## compose.yamlを作る

プロジェクトのルートに `compose.yaml` を作成します。

```yaml
services:
  app:
    image: node:22
    ports:
      - "3000:3000"
  db:
    image: postgres:16
```

この例では、アプリケーションを `http://localhost:3000` に公開し、PostgreSQLを別コンテナで起動します。

## 起動する

```sh
docker compose up -d
docker compose ps
```

`docker compose ps` で `app` と `db` が起動中になっていれば、コンテナの起動確認は完了です。動かない場合は、次のコマンドでログを確認します。

```sh
docker compose logs --tail=100
```

## 実運用で追加する設定

この最小構成には、データ永続化用のボリュームや接続用の環境変数を含めていません。開発で継続利用する場合は、プロジェクトの要件に合わせて追加してください。

## 訂正のポイント

- 「便利です」のような一般論を減らし、記事の目的を冒頭で示した
- 前提、設定、起動、トラブルシュートの順に整理した
- 下書きにない環境変数やボリュームの具体値は追加していない
- 不確かな断定を避け、最小構成と実運用上の注意を分けた
