# ChatGPTからRigを使う — remote MCP adapter

Rigには従来から`stdio`のMCP server (`scripts/mcp_server.py`) がある。これはローカルhost/agent向けであり、ChatGPTから直接接続する入口にはならない。

ChatGPTはremote MCP serverへ接続するため、このadapterはpackage-nativeな`rig-mcp` commandとして **Streamable HTTP** を提供する。実行ロジックをMCP側へ複製せず、すべて`rig-wb`の既存code pathへ委譲する。

> OpenAI側の提供条件・UIは変わりうる。この文書のChatGPT部分は2026-08-11時点。最新情報はOpenAIのDeveloper mode / MCP appsドキュメントを確認すること。

## 1. install

```bash
pip install 'rig-workbench[mcp]'
```

source checkoutなら:

```bash
pip install -e '.[mcp]'
```

## 2. read-onlyで起動する

対象repositoryを一つ固定して起動する。

```bash
rig-mcp \
  --repo /path/to/project \
  --transport streamable-http
```

既定値:

- bind: `127.0.0.1:8000`
- endpoint: `/mcp` (MCP Python SDKのStreamable HTTP既定値)
- repository: server起動時に固定
- write actions: **disabled**

利用できるread tool:

- `rig_status`
- `rig_board`
- `rig_diff`
- `rig_plan`

## 3. write actionsを許可する

```bash
rig-mcp \
  --repo /path/to/project \
  --transport streamable-http \
  --allow-write
```

追加で使えるtool:

- `rig_run`
- `rig_accept`
- `rig_discard`

`rig_accept`はMCP独自のmerge実装を持たない。`rig-wb wb accept`へ委譲するため、worktree、base branch、diff summary、acceptance gateなど既存の構造的前提をそのまま通る。

remote adapterからは`--force`を公開しない。`rig_discard`もtool argumentとして`confirm=true`が必要になる。

## 4. ChatGPTへつなぐ

ChatGPTはローカルMCP serverへ直接接続しない。二つの方法がある。

1. Rig MCP serverを認証付きHTTPS endpointとしてremote deployする。
2. 開発PC / private network上で動かす場合は、OpenAIが案内するSecure MCP Tunnelを使う。

ChatGPT側ではDeveloper modeでcustom MCP appを作り、remote endpoint (`.../mcp`) を登録してtool scanを行う。

OpenAI公式:

- Developer mode and MCP apps in ChatGPT: https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt
- Apps in ChatGPT: https://help.openai.com/en/articles/11487775-connectors-in-chatgpt

2026-08-11時点では、ChatGPT Webでcustom MCP appsを設定する。OpenAIの案内ではmobile appからMCP appsは利用できない。Full MCPのwrite/modify actionsはBusiness / Enterprise / Edu向けで、Proはdeveloper modeでread/fetch権限のMCP接続が対象になる。

## 5. このadapterの境界

```text
ChatGPT
   |
   | remote MCP / Streamable HTTP
   v
rig-mcp
   |
   | fixed argv, no shell
   v
python -m rig_workbench.cli
   |
   +--> plan / run
   +--> wb status / board / diff
   +--> wb accept / discard
   |
   v
Rig run state + worktree + acceptance gate
```

MCP serverは新しいauthorityではない。ChatGPTに緑色の成功表示が出ても、Rig coreのgateを迂回してよい理由にはならない。

### repository binding

一つのserver processは一つのrepository rootに固定する。ChatGPTから任意pathを渡して別repositoryへ移動するtoolは公開しない。

複数projectを扱う場合は、projectごとにserver instance / port / tunnel identityを分ける。これにより、ChatGPTへ渡したapp permissionとfilesystem authorityの境界を一致させやすい。

### command injection

recipe、task id、providerは識別子としてvalidationし、shellを介さずargvで`rig-wb`へ渡す。goalだけは自然文としてargvの一要素に渡す。

### output size

stdout/stderrはそれぞれ128 KiBでtruncateする。巨大なdiffやlogをMCP responseへ無制限に載せない。

## 6. local MCP clientで使う場合

同じentrypointはstdioも使える。

```bash
rig-mcp --repo /path/to/project --transport stdio
```

ChatGPT接続ではremote MCPが必要だが、MCP Inspectorやstdio対応hostでadapterのtool contractを確認するときに使える。

## 7. 最初に試す会話

read-only serverなら:

```text
Rigのboardを見て、止まっているtaskと次に人間が判断すべきものを教えて。
```

writeを有効にした環境なら:

```text
bugfix recipeをCodex providerでisolated runして。
終わったらdiffとgateを見せて。acceptはまだしないで。
```

この順序を推奨する。ChatGPTを「Rigを迂回して作業するagent」にするのではなく、Rigのcontrol planeを会話から操作するhostとして扱う。
