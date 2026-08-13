# ChatGPTからRig remote MCPを使う

Rigのserver contract、install、tool、安全境界はclient-neutralな
[`remote-mcp.md`](./remote-mcp.md) を参照する。この文書ではChatGPTとの接続だけを扱う。

> OpenAI側の提供条件とUIは変わりうる。この文書は2026-08-12時点。最新情報は
> OpenAI公式のDeveloper mode / MCP apps documentationを確認すること。

## 接続構成

`rig-mcp`は安全のためloopbackにしかbindせず、TLSやOAuthを終端しない。ChatGPTとの
接続は、次の二つを別の構成として選ぶ。

### Secure MCP Tunnel

OpenAIのtunnel clientを`rig-mcp`と同じprivate環境で動かし、その転送先をlocalの
`http://127.0.0.1:8000/mcp`にする。tunnel clientが対応している場合はstdioを転送先に
してもよい。ChatGPT appではURL接続ではなく`Connection = Tunnel`を選び、発行された
`tunnel_id`を指定する。tunnel用のHTTPS endpointをappのdirect server URLとして
登録する手順ではない。

```bash
rig-mcp \
  --repo /path/to/project \
  --transport streamable-http \
  --allow-unauthenticated-http
```

### public HTTPS endpoint

別案は、認証付きHTTPS reverse proxyを公開し、外側の`https://.../mcp`からlocalの
`http://127.0.0.1:8000/mcp`へ転送するdirect URL構成である。proxyがTLSと認証を担う。
public endpointでOAuthを使うなら、ChatGPTのMCP OAuth要件を満たす必要がある。

`rig-mcp`のtransport securityはloopbackのHost/Originだけを許可する。proxyはupstreamの
`Host`を実際に設定したloopback host（例: `127.0.0.1:8000`）へ書き換え、外部の`Origin`
をそのまま転送しない。`Origin`は除去するか、許可されたloopback originへ書き換える。
`rig-mcp`を`0.0.0.0`へ直接公開する構成はsupportしない。
`--allow-unauthenticated-http`は認証機能ではなく、adapterのHTTP endpoint自体には認証が
ないことへのacknowledgementである。Host/Origin検証も認証ではない。localhostは同じhostの
他user/processから到達しうるので、dedicated single-user host/containerを使うか、loopbackへ
直接到達できる主体を制限する。

OpenAI公式:

- Secure MCP Tunnel: https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
- Developer mode and MCP apps in ChatGPT: https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt
- MCP server authentication: https://developers.openai.com/plugins/build/auth

## permissionを段階的に開く

最初はread-onlyで接続し、`rig_status`、`rig_board`、`rig_diff`、`rig_plan`を確認する。
writeが必要なserver instanceだけ`--allow-write`を付けて再起動する。write toolを使える
ChatGPT planやworkspace条件はOpenAI側の最新documentationで確認する。

```bash
rig-mcp \
  --repo /path/to/project \
  --transport streamable-http \
  --allow-unauthenticated-http \
  --allow-write \
  --operator-id alice@example.com
```

外側のtunnel/proxyは、このinstanceで指定した一人のoperatorだけを認証しなければならない。
adapterはChatGPT callerのidentity/scopeをRigへ伝播しないため、multi-user/shared-workspaceで
同じwrite-enabled HTTP instanceを共有する構成はsupportしない。`--operator-id`はchildの
`RIG_ACTOR`と`RIG_USER`に設定される。principalごとのOAuth/scope伝播は将来の課題である。

serverを再起動しても、ChatGPT側のaction snapshotは自動更新・自動有効化されない。
appの管理画面でactionをrefreshし、追加・変更された定義をreviewしてから必要なactionを
明示的に有効化する。新しいactionは既定でdisabledになる。実際のreview/publish手順は
workspaceごとに変わりうるため、上記のDeveloper mode公式helpを確認する。

write-enabledでも`rig_run`は常にGit worktreeをisolateし、`rig_accept`はtask idを要求し
forceを公開しない。ただしworktree isolationはprovider/recipeのexternal effectをsandbox
しない。acceptの前にdiffとgateを確認する。

最初のread-only会話例:

```text
Rigのboardを見て、止まっているtaskと次に人間が判断すべきものを教えて。
```

write-enabled instanceの例:

```text
bugfix recipeをCodex providerでisolated runして。
終わったらdiffとgateを見せて。acceptはまだしないで。
```

ChatGPTはRigを迂回する新しいauthorityではなく、Rig control planeを会話から操作する
MCP clientの一つである。
