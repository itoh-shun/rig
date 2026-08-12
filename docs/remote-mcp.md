# Rig remote MCP adapter

`rig-mcp`は、MCP clientからRigのcontrol planeを操作するpackage-nativeな
adapterである。ChatGPT専用ではなく、Streamable HTTPまたはstdioを扱えるMCP
clientで同じtool contractを利用できる。ChatGPT固有の接続手順は
[`chatgpt-mcp.md`](./chatgpt-mcp.md) に分離している。

従来の`scripts/mcp_server.py`ではなく`rig-wb`の既存code pathへ委譲するため、
route、worktree、gate、acceptの意味をMCP側で再実装しない。

## install

```bash
pip install 'rig-workbench[mcp]'
```

source checkoutではoptional extraも含めてinstallする。

```bash
pip install -e '.[mcp]'
```

MCP SDKは`mcp>=1.28.1,<2`を使う。v2はserver APIが異なるため、このadapterの
v1 contractでは意図的に除外している。

## repositoryを固定して起動する

対象pathは`.rig/`を持つGit top-levelそのものでなければならない。subdirectoryや
その中に置いた擬似`.rig/`は受け付けない。

```bash
rig-mcp \
  --repo /path/to/project \
  --transport streamable-http \
  --allow-unauthenticated-http
```

既定値:

- transport: `stdio`
- HTTP bind（選択時）: `127.0.0.1:8000`
- endpoint: `/mcp`
- repository: process起動時に一つへ固定
- write tools: disabled
- per-command timeout: 1800秒

Streamable HTTPはloopback addressにしかbindできない。`0.0.0.0`、LAN address、
public hostnameは起動時に拒否される。read-onlyでも`--allow-unauthenticated-http`
（`RIG_MCP_ALLOW_UNAUTHENTICATED_HTTP=1`）による明示的なacknowledgementがなければ起動
しない。このflagは認証を追加せず、単一operator向けのunauthenticated HTTPを選択した
ことを確認するだけである。remote clientとの接続は二通りある。

- Secure MCP Tunnelでは、customer-run tunnel clientの転送先をlocalの
  `http://127.0.0.1:8000/mcp`（対応する場合はstdioも可）にする。OpenAI product側は
  tunnel connectionと`tunnel_id`を選ぶ。tunnelのHTTPS endpointをdirect URLとして
  登録する構成ではない。
- public direct URLでは、認証付きHTTPS reverse proxyを外側に置き、upstreamを
  `http://127.0.0.1:8000/mcp`にする。

`rig-mcp`自体はTLSやOAuthを終端せず、外側のtunnel/proxyが認証を担う。public OAuthを
使う場合は接続するMCP clientのOAuth要件を満たす必要がある。reverse proxyはupstreamの
`Host`を設定済みloopback hostへ書き換え、外部の`Host`/`Origin`をそのまま転送しない。
`Origin`は除去するか、許可されたloopback originへ書き換える。

Host/Origin検証はDNS rebinding対策であり、callerの認証ではない。localhost endpointは
同じhost上の他user/processからも到達しうるため、dedicated single-user host/containerで
動かすか、loopbackへの直接到達を制限したtunnel/proxyを使う。一つのHTTP instanceは
一つの認証済みprincipalだけに対応させる。caller identity/scopeをMCP requestからRigへ
伝播しないため、multi-user/shared-workspace HTTPはsupportしない。principalごとのOAuthと
scope伝播は将来の課題である。

OpenAI client向けの詳細:

- Secure MCP Tunnel: https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
- MCP server authentication: https://developers.openai.com/plugins/build/auth

利用できるloopback hostは`localhost`またはloopback IP (`127.0.0.0/8`, `::1`)。
HTTP transportではMCP SDKのHost/Origin検証を明示的に有効化する。
`--host`と`--port`はStreamable HTTP専用で、stdioでは無視する。

## tools

read-only起動では次のtoolだけを広告する。

- `rig_status`
- `rig_board`
- `rig_diff`
- `rig_plan`

stdioでwriteを許可する場合:

```bash
rig-mcp --repo /path/to/project --allow-write
```

HTTPでwriteを許可する場合は、外側のproxy/tunnelが認証する唯一のprincipalを明示する。

```bash
rig-mcp \
  --repo /path/to/project \
  --transport streamable-http \
  --allow-unauthenticated-http \
  --allow-write \
  --operator-id alice@example.com
```

`--operator-id`はchildの`RIG_ACTOR`と`RIG_USER`を同じ値へ固定する。一つのserver instanceを
複数principalで共有してはならない。stdioではoperator idを強制せず、既存のRig identity
解決を使う。

このときだけ次のtoolがserverのtool listへ追加される。

- `rig_run`: 常にisolated worktreeで実行する
- `rig_accept`: 明示したtaskだけを通常のaccept gate経由で反映する
- `rig_discard`: 明示したtaskと`confirm=true`を要求する

`rig_accept`にforce引数はない。read-only serverはwrite toolを広告してから実行時に
拒否するのではなく、最初から登録しない。MCP annotationは副作用のhintを正しく示すが、
実際のenforcementはtool registration、gateway guard、Rig core gateが担う。

## safety boundary

```text
MCP client
    |
    | stdio / loopback Streamable HTTP
    v
rig-mcp (one fixed Git + .rig root)
    |
    | fixed argv, no shell, bounded stdin/result
    v
python -I -m rig_workbench.cli
    |
    +-- plan / isolated run
    +-- wb status / board / diff
    +-- wb accept / discard
```

- recipe、task id、providerはleading英数字かつ128文字以下のidentifierに限定する。
- goalはprocess argvへ入れず、`--goal-stdin`へUTF-8で渡す。上限は1 MiB。
- childはasync subprocessとして新しいprocess groupで起動する。timeoutまたはMCP callの
  cancellation時はgroup全体をterminateし、残ればkillする。
- stdout/stderr pipeは常に最後までdrainするが、memoryに保持するのはstdout 128 KiB、
  stderr 16 KiBまでで、超過分は読み捨てる。返すUTF-8 responseも同じbyte上限内である。
- mutating call (`run`/`accept`/`discard`) はserver instanceごとのasync lockで直列化する。
  read callは並行実行できる。isolated worktreeはexternal effectをsandboxしないため、
  `rig_run`のMCP annotationはdestructiveとする。
- child環境からGit repository/object/index/configを差し替える環境変数（`GIT_DIR`、
  `GIT_COMMON_DIR`、`GIT_OBJECT_DIRECTORY`、`GIT_INDEX_FILE`、`GIT_CONFIG_*`等）と一時的な
  `RIG_ALLOW_PROJECT_*` trust consentを除去する。project assetのtrustはremote toolで
  付与せず、operatorがlocalで事前承認する。
- command failure、timeout、validation failureは成功payloadの`ok=false`ではなく
  MCP tool error (`isError=true`) になる。
- childは`python -I`で起動する。対象repositoryに置かれた同名Python moduleをcwdから
  importしないため、`rig-workbench[mcp]`を実行環境へinstallしておく必要がある。
- serverに緑色のtool resultが表示されても、品質の証明やRig gateを迂回する根拠にはならない。

環境変数でも同じ設定を指定できる。

| CLI | environment |
|---|---|
| `--repo` | `RIG_MCP_REPO` |
| `--transport` | `RIG_MCP_TRANSPORT` |
| `--host` | `RIG_MCP_HOST` |
| `--port` | `RIG_MCP_PORT` |
| `--allow-write` | `RIG_MCP_ALLOW_WRITE` |
| `--allow-unauthenticated-http` | `RIG_MCP_ALLOW_UNAUTHENTICATED_HTTP` |
| `--operator-id` | `RIG_MCP_OPERATOR_ID` |
| `--timeout` | `RIG_MCP_TIMEOUT` |

## stdio client

同じentrypointをlocal MCP hostやMCP Inspectorからstdioで使える。

```bash
rig-mcp --repo /path/to/project --transport stdio
```

`scripts/mcp_server.py`はstdlib-onlyのhistorical/local stdio adapterであり、agent、CI、
別processから利用できる。`rig-mcp`はoptional MCP SDKを使うpackage-native adapterで、
公開するtool contractもlegacy adapterとは異なる。両者は相互に置換可能ではない。

SDK clientとのinitialize、tool listing、tool error contractは自動testしている。個別hostの
UIや認証方式は各host側の仕様であり、実機確認していないclientを互換確認済みとは扱わない。
