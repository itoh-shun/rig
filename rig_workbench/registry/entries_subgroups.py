"""The five sub-groups that hang off `rig-wb`: `govern`, `pack`, `eval`, `baseline`, `githooks`.

Read `model.py` before this file. Everything here is declaration — no handler, no callable,
no import of the code that runs any of it (`docs/v3-architecture-design-brief.ja.md` §9:
intent is the source of truth, the CLI is a projection, and rewiring the surfaces onto this
table is stage 3).

Three decisions shape the table, and each of them was a choice between two defensible shapes.

**How deep `verb` goes.** `Capability.verb` admits a space-separated pair "where the surface
nests a level `PARENTS` does not name", and `command_path` has to stay *the literal thing a
person types*. That rule sorts the two candidates in this slice differently, so the line is
drawn on whether the second word is one a person can leave out:

* `pack source` is an argparse sub-subparser with `required=True`. `rig-wb pack source` alone
  is a usage error; only `pack source list|add|remove` is a command anyone can run. So those
  are three entries with two-word verbs. Splitting them is not bookkeeping: `list` reads and
  `add`/`remove` write, and `--scheme`/`--url` are required for `add` and meaningless for the
  other two, so a single `pack.source` record could not state its own `effect_class` or its
  own flags without lying about two thirds of what it covers.
* `govern policy`, `govern approve`, `govern waiver` and `govern audit` take an *optional*
  positional with a default (`action`, `nargs="?"`). `rig-wb govern policy` is a complete
  command meaning `policy show`. The second word is an argument, so it is declared as a
  `choice` flag and the verb stays one word. Where the chosen action changes what is
  disturbed — `govern approve status` reads, `grant` writes the ledger — `effect_class` takes
  the strongest, as the field's own docs require, and `effect_line` names which action does
  which.

**`network` is answered from the code, and one recorded correction no longer holds.**
`model.Capability.network` says `pack install` is `never` because "`packs/installer.py`
refuses URL sources outright and resolves `official:` against a catalogue that ships in the
package". The first half is still true (`_resolve_source` raises on `http(s)://`) and the
second half is no longer the whole story: `install_pack` first tries `parse_spec`, and a
`<source>:<pack>@<version>` spec goes to `sources.resolve_revision` → `git ls-remote` and
then `fetch_revision` → `git fetch`. `tests/test_pack_sources.py` drives exactly that path,
and `tests/test_cli_surface_contract.py` excludes `pack install` from its smoke runs because
it "resolves a source (git clone)". So `pack.install` is declared `sometimes` — never for a
directory, a zip, a tar or a `domain:`/`official:` alias, and out to the source for a named
spec unless that source's scheme is `git+file`. `pack update`, `pack outdated` and
`pack verify-sources` reach out through the same three functions and are `sometimes` for the
same reason. The `effect_class` half of that correction is untouched and kept.

**`output_schema` is a `rig.<name>/v<N>` id or nothing.** Only `govern` mints any in this
slice; an AST scan of `eval/`, `packs/`, `baseline.py` and `githooks.py` for string literals
matching `rig\\.[a-z0-9_.-]+/v\\d+` returns none. Those commands do emit structured JSON, but
they version it with their own integer key (`pack_test_schema_version`,
`eval_gate_schema_version`, `baseline_schema_version`), which is not the shape this field
names and not something `tests/test_schema_registry.py` pins. Declaring one would put a
public id into the registry that no command can be driven to produce. The four that are real
are each in that test's frozen set, and "emits" is read as *produces as its answer* whether
that answer is printed (`rig.effective-policy/v1` from `govern policy --json`) or written
(`rig.org/v2` from `govern init`, `rig.policy/v2` from `govern migrate`, `rig.waivers/v2`
from `govern waiver grant`), because a caller has to parse it either way.

Exit codes are what the source returns, and `govern` is the odd one: it defines
`EXIT_OK, EXIT_ERROR, EXIT_NONCONFORMANT = 0, 1, 3` and `_err` returns 1, so a govern refusal
lands on the code `exitcodes.py` reserves for a verdict. That is recorded here as it is, not
as it ought to be. `govern can`'s 0/3 split is the one pair in this slice that
`tests/test_exit_code_surface.py` measured through a real process; everything else is read
off the returns.
"""

from __future__ import annotations

from .model import Capability, ExitCode, Flag

# ── shared exit codes ────────────────────────────────────────────────────────
# Written out per capability rather than shared as constants where the meaning differs;
# these three are the ones whose meaning really is identical everywhere they appear.
_USAGE = ExitCode(code=2, meaning="引数が足りない・不正で、コマンドを実行できなかった")
_PACK_ERROR = ExitCode(
    code=2, meaning="PackError: pack を読めない・契約に反する・要求を満たせない"
)
_EVAL_ERROR = ExitCode(
    code=2, meaning="EvalCaseError: case や結果を読めない・検証できない"
)

# `--scope` on the pack verbs, and `--root`, are declared once: the same two flags are
# attached to eleven parsers by the same loop in `packs/cli.py`.
_SCOPE = Flag(
    name="--scope",
    type="choice",
    help="どのインストール階層を見るか（org は RIG_ORG_HOME が要る）",
    choices=("project", "user", "org"),
    default="project",
)
_ROOT = Flag(name="--root", type="path", help="scope の既定ルートの代わりに使うディレクトリ")
_JSON = Flag(name="--json", type="bool", help="機械可読な JSON で出力する")

_PROVIDERS = ("mock", "claude", "codex", "command")


# ── govern ───────────────────────────────────────────────────────────────────
GOVERN: tuple[Capability, ...] = (
    Capability(
        id="govern.init",
        parent="govern",
        verb="init",
        intent="start holding this repository to a shared standard, with something to edit "
               "rather than a blank page",
        preconditions=("git-repo", "org-binding-absent"),
        effect_line=".rig/org.json と starter policy (.rig/policy/org.json) を書き出し、"
                    "監査台帳に policy.init を追記します",
        effect_class="writes-state",
        network="never",
        flags=(
            Flag(name="--org", type="string", help="org 識別子（例: acme）", required=True),
            Flag(name="--team", type="string", help="team 識別子（例: team-a）"),
            Flag(name="--layer", type="string-list",
                 help="既存の policy 層のパス。順に適用され、繰り返し指定できる"),
            Flag(name="--force", type="bool", help="既存のファイルを上書きする"),
        ),
        output_schema="rig.org/v2",
        exit_codes=(
            ExitCode(code=0, meaning="binding と starter policy を書き出した"),
            ExitCode(code=1, meaning="対象ファイルが既にある（--force なしでは上書きしない）"),
            _USAGE,
        ),
    ),
    Capability(
        id="govern.migrate",
        parent="govern",
        verb="migrate",
        intent="carry the access and gate settings this team already tuned into the new "
               "shape instead of retyping them",
        preconditions=("git-repo", "legacy-access-or-gates-file", "org-known"),
        effect_line="v1 の .rig/access.json と .rig/gates.json を畳んだ policy 文書を "
                    ".rig/policy/<id>.json に書き出します（元のファイルはそのまま動きます）",
        effect_class="writes-state",
        network="never",
        flags=(
            Flag(name="--org", type="string", help="org 識別子（既定は .rig/org.json のもの）"),
            Flag(name="--scope", type="choice", help="この層が効く範囲",
                 choices=("org", "team", "project"), default="project"),
            Flag(name="--team", type="string", help="team 識別子（--scope team では必須）"),
            Flag(name="--id", type="string", help="policy 文書の id", default="migrated"),
            Flag(name="--out", type="path", help=".rig/policy/<id>.json ではなくここに書く"),
            Flag(name="--force", type="bool", help="既存のファイルを上書きする"),
        ),
        output_schema="rig.policy/v2",
        exit_codes=(
            ExitCode(code=0, meaning="policy 層を書き出した"),
            ExitCode(code=1, meaning="移行元がない・org が分からない・出力先が既にある"),
            _USAGE,
        ),
    ),
    Capability(
        id="govern.policy",
        parent="govern",
        verb="policy",
        intent="see which rules are actually in force here, and whether the layers they "
               "come from stack without any of them loosening the one before it",
        preconditions=("git-repo", "policy-layers-resolvable"),
        effect_line="有効な policy を読み出して表示します。lint も層を検証するだけで、"
                    "何も書き換えません",
        effect_class="read-only",
        network="never",
        flags=(
            Flag(name="action", type="choice", help="表示するか、層を検証するか",
                 choices=("show", "lint"), default="show"),
            Flag(name="paths", type="string-list",
                 help="lint のとき、解決された層の代わりに検証する文書"),
            Flag(name="--json", type="bool", help="show のとき機械可読な JSON で出力する"),
        ),
        output_schema="rig.effective-policy/v1",
        exit_codes=(
            ExitCode(code=0, meaning="表示した、または層はすべて妥当で緩めていない"),
            ExitCode(code=1, meaning="policy を読み出せなかった"),
            ExitCode(code=3, meaning="lint が不正な層、または層をまたぐ緩めを見つけた"),
            _USAGE,
        ),
    ),
    Capability(
        id="govern.whoami",
        parent="govern",
        verb="whoami",
        intent="find out what I am allowed to do in this repository, and which roles are "
               "giving me that",
        preconditions=("git-repo", "policy-layers-resolvable", "actor-identity"),
        effect_line="現在の actor の role と permission を読み出して表示します",
        effect_class="read-only",
        network="never",
        flags=(Flag(name="--actor", type="string", help="別の identity について尋ねる"),),
        exit_codes=(
            ExitCode(code=0, meaning="role と permission を表示した"),
            ExitCode(code=1, meaning="policy を読み出せなかった"),
            _USAGE,
        ),
    ),
    Capability(
        id="govern.can",
        parent="govern",
        verb="can",
        intent="get a yes or no on one specific thing before something else depends on the "
               "answer",
        preconditions=("git-repo", "policy-layers-resolvable", "actor-identity",
                       "known-permission"),
        effect_line="permission を 1 件だけ判定して結果を表示します（何も書き換えません）",
        effect_class="read-only",
        network="never",
        flags=(
            Flag(name="permission", type="string", help="判定する permission 名",
                 required=True),
            Flag(name="--actor", type="string", help="別の identity について尋ねる"),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="許可されている"),
            ExitCode(code=1, meaning="policy を読み出せない、または知らない permission 名"),
            ExitCode(code=3, meaning="拒否されている"),
            _USAGE,
        ),
    ),
    Capability(
        id="govern.approve",
        parent="govern",
        verb="approve",
        intent="sign off on somebody's change, or find out what sign-off it is still "
               "waiting for and from whom",
        preconditions=("git-repo", "task-exists", "actor-identity", "permission-approve"),
        effect_line="grant と deny は承認の決定を記録し、監査台帳にも追記します。"
                    "status は読み出すだけです",
        effect_class="writes-state",
        network="never",
        flags=(
            Flag(name="action", type="choice", help="状況を見るか、承認するか、却下するか",
                 choices=("status", "grant", "deny"), default="status"),
            Flag(name="task_id", type="string", help="既定は最新の task"),
            Flag(name="--note", type="string", help="理由（決定と一緒に記録される）"),
            Flag(name="--actor", type="string", help="この identity として決定を記録する"),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="必要な承認が揃っている、または承認自体が不要"),
            ExitCode(code=1, meaning="task が見つからない、または承認する権限がない"),
            ExitCode(code=3, meaning="必要な承認がまだ揃っていない"),
            _USAGE,
        ),
    ),
    Capability(
        id="govern.waiver",
        parent="govern",
        verb="waiver",
        intent="let one named rule slide for a stated reason and a fixed period, or take "
               "that exception back before it lapses",
        preconditions=("git-repo", "policy-layers-resolvable", "actor-identity",
                       "permission-waiver", "waiver-criterion-named"),
        effect_line="grant は .rig/waivers.json に免除を追記し、revoke はそれを失効させます"
                    "（どちらも監査台帳に残ります）。list は読み出すだけです",
        effect_class="writes-state",
        network="never",
        flags=(
            Flag(name="action", type="choice", help="一覧するか、出すか、取り消すか",
                 choices=("list", "grant", "revoke"), default="list"),
            Flag(name="id", type="string", help="waiver id（grant / revoke で使う）"),
            Flag(name="--criterion", type="string-list",
                 help="この免除が何を許すのか。grant では必須で、繰り返し指定できる"),
            Flag(name="--reason", type="string", help="なぜこの例外が要るのか"),
            Flag(name="--expires", type="string",
                 help="YYYY-MM-DD（既定は policy が許す最大期間）"),
            Flag(name="--scope", type="string", help="どの範囲に効かせるか", default="*"),
            Flag(name="--actor", type="string", help="この identity として実行する"),
        ),
        output_schema="rig.waivers/v2",
        exit_codes=(
            ExitCode(code=0, meaning="一覧した、または免除を出した／取り消した"),
            ExitCode(code=1, meaning="権限がない、--criterion がない、waiver が不正"),
            _USAGE,
        ),
    ),
    Capability(
        id="govern.audit",
        parent="govern",
        verb="audit",
        intent="read the record of who changed what, and show that nobody has edited that "
               "record afterwards",
        preconditions=("git-repo", "audit-ledger", "permission-audit-export"),
        effect_line="log と verify は台帳を読むだけです。export は台帳を書き出したうえで、"
                    "その書き出し自体を台帳に追記します",
        effect_class="writes-state",
        network="never",
        flags=(
            Flag(name="action", type="choice", help="読むか、鎖を検証するか、書き出すか",
                 choices=("log", "verify", "export"), default="log"),
            Flag(name="--limit", type="int", help="log のとき、最新 N 件だけ表示する"),
            Flag(name="--action", type="string", help="action 名で絞り込む"),
            Flag(name="--since", type="string", help="YYYY-MM-DD 以降の項目だけ"),
            Flag(name="--format", type="choice", help="export の形式",
                 choices=("jsonl", "csv", "markdown"), default="jsonl"),
            Flag(name="--out", type="path", help="export のとき、このファイルに書く"),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="表示した、鎖は無傷だった、または書き出した"),
            ExitCode(code=1, meaning="書き出す権限がない、または形式が不正"),
            ExitCode(code=3, meaning="台帳の鎖が壊れている（改竄の疑い）"),
            _USAGE,
        ),
    ),
    Capability(
        id="govern.conformance",
        parent="govern",
        verb="conformance",
        intent="find out whether this repository actually clears the standard it says it "
               "follows, and by how much",
        preconditions=("git-repo", "org-binding", "policy-layers-resolvable"),
        effect_line="この repository を有効な policy に照らして採点し、"
                    "検査ごとの結果を表示します（何も書き換えません）",
        effect_class="read-only",
        network="never",
        flags=(
            Flag(name="path", type="path", help="測る repository（既定は現在のもの）"),
            Flag(name="--since-days", type="int",
                 help="測定対象にする run の期間", default=90),
            _JSON,
        ),
        exit_codes=(
            ExitCode(code=0, meaning="pass か warn（fail ではない）"),
            ExitCode(code=3, meaning="fail、または policy を読めず測れなかった"),
            _USAGE,
        ),
    ),
    Capability(
        id="govern.rollup",
        parent="govern",
        verb="rollup",
        intent="see how several projects are doing against the same standard, side by side "
               "in one table",
        preconditions=("project-paths-exist", "org-binding"),
        effect_line="指定した repository をそれぞれローカルに読み取り、org / team 単位に"
                    "集計して表示します（ネットワークには出ません）",
        effect_class="read-only",
        network="never",
        flags=(
            Flag(name="paths", type="string-list", help="repository のパス", required=True),
            Flag(name="--scan", type="bool",
                 help="渡したディレクトリの直下から .rig/org.json を持つものを探す"),
            Flag(name="--since-days", type="int",
                 help="測定対象にする run の期間", default=90),
            _JSON,
        ),
        exit_codes=(
            ExitCode(code=0, meaning="集計した（fail の project はない）"),
            ExitCode(code=1, meaning="集計する project が 1 つもなかった"),
            ExitCode(code=3, meaning="少なくとも 1 つの project が fail"),
            _USAGE,
        ),
    ),
)


# ── pack ─────────────────────────────────────────────────────────────────────
PACK: tuple[Capability, ...] = (
    Capability(
        id="pack.init",
        parent="pack",
        verb="init",
        intent="start a new pack from something that already works, instead of hand-writing "
               "a canonical manifest",
        preconditions=("target-directory-free", "pack-type-chosen"),
        effect_line="新しい pack のディレクトリを作り、pack.yaml / compatibility.yaml と"
                    "資産ディレクトリ一式を書き出します",
        effect_class="writes-worktree",
        network="never",
        flags=(
            Flag(name="id", type="string", help="pack の id", required=True),
            Flag(name="--kind", type="choice", help="どの階層のための pack か",
                 choices=("core", "official", "domain", "project"), default="project"),
            Flag(name="--type", type="choice",
                 help="pack が何を持ち何を実行できるか。既定はない（決め忘れを引き受けない）",
                 choices=("knowledge", "policy", "reviewer", "skill", "workflow", "tool"),
                 required=True),
            Flag(name="--root", type="path", help="作成先のルート", default=".rig/packs"),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="pack を作成し、次の手順を表示した"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.validate",
        parent="pack",
        verb="validate",
        intent="check one pack against the contract it has to satisfy before anybody else "
               "installs or ships it",
        preconditions=("pack-dir", "manifest-canonical", "assets-declared", "engine-compatible",
                       "approved-eval-case"),
        effect_line="pack の manifest・宣言された資産・ハッシュ・評価ケースを検証して"
                    "結果を表示します（何も書き換えません）",
        effect_class="read-only",
        network="never",
        flags=(
            Flag(name="path", type="path", help="検証する pack（既定は現在のディレクトリ）"),
            Flag(name="--global", type="bool",
                 help="1 つではなく、インストール済みの全階層をまとめて検証する"),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="pack は契約を満たしている"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.doctor",
        parent="pack",
        verb="doctor",
        intent="work out why an installed pack is not doing what I expected, across every "
               "tier at once rather than one pack at a time",
        preconditions=("none",),
        effect_line="インストール済みの pack と lock を階層横断で診断し、"
                    "所見を一覧します（何も書き換えません）",
        effect_class="read-only",
        network="never",
        flags=(
            Flag(name="path", type="path", help="1 つの pack だけを診断する"),
            _JSON,
        ),
        exit_codes=(
            ExitCode(code=0, meaning="ok、または言うべきことはあるが壊れてはいない (warning)"),
            ExitCode(code=1, meaning="failed: 壊れている所見がある"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.sync",
        parent="pack",
        verb="sync",
        intent="I added or deleted a file inside my pack and want its manifest to say what "
               "is actually on disk",
        preconditions=("pack-dir", "pack-unsigned", "every-file-in-an-asset-dir"),
        effect_line="pack.yaml の assets と hashes をディスクの実体から作り直して"
                    "上書きします（署名済みの pack は拒否します）",
        effect_class="writes-worktree",
        network="never",
        flags=(Flag(name="path", type="path", help="対象の pack（既定は現在のディレクトリ）"),),
        exit_codes=(
            ExitCode(code=0, meaning="宣言とハッシュを作り直した"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.source.list",
        parent="pack",
        verb="source list",
        intent="see which named places this project is allowed to fetch packs from",
        preconditions=("none",),
        effect_line=".rig/sources.json に宣言された source を読み出して一覧します",
        effect_class="read-only",
        network="never",
        exit_codes=(
            ExitCode(code=0, meaning="宣言された source を表示した"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.source.add",
        parent="pack",
        verb="source add",
        intent="give a repository of packs a short name here, so nothing anyone installs "
               "ever has to carry a URL",
        preconditions=("source-name-free", "source-name-not-reserved", "source-scheme-known"),
        effect_line=".rig/sources.json に source 名と URL テンプレートを追記します"
                    "（資格情報は読みも保存もしません）",
        effect_class="writes-state",
        network="never",
        flags=(
            Flag(name="name", type="string", help="この source を呼ぶ名前", required=True),
            Flag(name="--scheme", type="choice", help="どう取りに行くか",
                 choices=("git+ssh", "git+https", "git+file"), required=True),
            Flag(name="--url", type="string", help="{pack} を含む URL テンプレート",
                 required=True),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="source を宣言した"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.source.remove",
        parent="pack",
        verb="source remove",
        intent="stop this project from being able to pull anything further from that place",
        preconditions=("source-declared",),
        effect_line=".rig/sources.json から source の宣言を消します"
                    "（インストール済みの pack はそのまま残ります）",
        effect_class="writes-state",
        network="never",
        flags=(Flag(name="name", type="string", help="消す source の名前", required=True),),
        exit_codes=(
            ExitCode(code=0, meaning="宣言を消した"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.export",
        parent="pack",
        verb="export",
        intent="take a pack out of this repository so it can live on its own and be shared",
        preconditions=("pack-dir", "export-target-free"),
        effect_line="pack を --to のディレクトリに書き出し、git で公開する手順を表示します",
        effect_class="writes-worktree",
        network="never",
        flags=(
            Flag(name="path", type="path", help="書き出す pack", required=True),
            Flag(name="--to", type="path", help="書き出し先のディレクトリ", required=True),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="書き出して、次の手順を表示した"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.bundle",
        parent="pack",
        verb="bundle",
        intent="produce the one file somebody else can install from, with a digest they can "
               "check it by",
        preconditions=("pack-dir", "manifest-canonical"),
        effect_line="pack を zip に固めて dist/（または --to）に書き出し、sha256 を表示します",
        effect_class="writes-worktree",
        network="never",
        flags=(
            Flag(name="path", type="path", help="固める pack", required=True),
            Flag(name="--to", type="path", help="出力先 zip（既定 dist/<id>-<version>.zip）"),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="zip を書き出し、sha256 を表示した"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.verify-sources",
        parent="pack",
        verb="verify-sources",
        intent="confirm the packs I installed are still exactly the bytes I pinned, and that "
               "nobody has moved a tag under me",
        preconditions=("pack-lock", "source-declared", "git-available", "source-reachable"),
        effect_line="lock された git 由来の pack ごとに source の ref を問い合わせて照合します"
                    "（git+ssh / git+https の source ならネットワークに出ます）",
        effect_class="read-only",
        network="sometimes",
        flags=(_SCOPE, _ROOT),
        exit_codes=(
            ExitCode(code=0, meaning="全て一致した、またはこの scope に git 由来の pack がない"),
            ExitCode(code=1, meaning="digest がずれた・source が未宣言・source に届かない"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.list",
        parent="pack",
        verb="list",
        intent="see what is installed here, where each one came from, and whether its "
               "publisher was ever verified",
        preconditions=("scope-root-resolvable",),
        effect_line="この scope にインストール済みの pack を読み出して一覧します",
        effect_class="read-only",
        network="never",
        flags=(_SCOPE, _ROOT),
        exit_codes=(
            ExitCode(code=0, meaning="一覧した（0 件でも 0）"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.outdated",
        parent="pack",
        verb="outdated",
        intent="find out which installed packs have a newer version waiting, before "
               "deciding whether to move",
        preconditions=("pack-lock", "source-declared", "git-available", "source-reachable"),
        effect_line="lock された git 由来の pack ごとに新しい tag を問い合わせて一覧します"
                    "（git+ssh / git+https の source ならネットワークに出ます）",
        effect_class="read-only",
        network="sometimes",
        flags=(_SCOPE, _ROOT),
        exit_codes=(
            ExitCode(code=0, meaning="全て最新、またはこの scope に git 由来の pack がない"),
            ExitCode(code=1, meaning="新しい版がある、または source を読めなかった pack がある"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.info",
        parent="pack",
        verb="info",
        intent="look up one installed pack's version, origin and verification state",
        preconditions=("scope-root-resolvable", "pack-installed"),
        effect_line="指定した pack の記録を読み出して表示します",
        effect_class="read-only",
        network="never",
        flags=(
            Flag(name="pack", type="string", help="調べる pack の id", required=True),
            _SCOPE, _ROOT, _JSON,
        ),
        exit_codes=(
            ExitCode(code=0, meaning="情報を表示した"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.explain",
        parent="pack",
        verb="explain",
        intent="find out what this pack actually contributes to a prompt, and which of its "
               "pieces another tier is quietly overriding",
        preconditions=("scope-root-resolvable", "pack-installed"),
        effect_line="この pack が提供する prompt surface と、"
                    "そのどれが他の階層に覆われているかを表示します",
        effect_class="read-only",
        network="never",
        flags=(
            Flag(name="pack", type="string", help="調べる pack の id", required=True),
            _SCOPE, _ROOT, _JSON,
        ),
        exit_codes=(
            ExitCode(code=0, meaning="surface を表示した（0 件でも 0）"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.knowledge",
        parent="pack",
        verb="knowledge",
        intent="find which installed packs claim to know about a subject, and who reviewed "
               "that claim and when",
        preconditions=("scope-root-resolvable",),
        effect_line="インストール済み pack の knowledge 宣言を読み出し、"
                    "topic と scope で絞って一覧します",
        effect_class="read-only",
        network="never",
        flags=(
            Flag(name="--topic", type="string-list",
                 help="繰り返し指定可。どれか 1 つに合えば一致とみなす"),
            Flag(name="--scope", type="string-list",
                 help="繰り返し指定可。`product` は配下すべて、`product:x` は厳密一致"),
            Flag(name="--scope-filter", type="choice", help="どのインストール階層を見るか",
                 choices=("project", "user", "org"), default="project"),
            _ROOT, _JSON,
        ),
        exit_codes=(
            ExitCode(code=0, meaning="一覧した（0 件でも 0）"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.update",
        parent="pack",
        verb="update",
        intent="move an installed pack to a different version without risking being left "
               "with neither version if it goes wrong",
        preconditions=("pack-lock", "pack-installed", "git-sourced-pack", "source-declared",
                       "git-available", "source-reachable", "attestation-key"),
        effect_line="新しい版を取得・検証してからディレクトリを入れ替え、lock を書き換えます"
                    "（git+ssh / git+https の source ならネットワークに出ます）",
        effect_class="writes-worktree",
        network="sometimes",
        flags=(
            Flag(name="pack", type="string", help="動かす pack の id", required=True),
            Flag(name="--to", type="string", help="移動先のバージョン", required=True),
            _SCOPE, _ROOT,
            Flag(name="--allow-unverified", type="bool",
                 help="署名のない pack を project scope に限って許す"),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="入れ替えて lock を更新した"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.install",
        parent="pack",
        verb="install",
        intent="add somebody else's pack to this project, and have rig record exactly which "
               "bytes it took so the same spec never means something different later",
        preconditions=("scope-root-resolvable", "pack-source-resolvable", "pack-lock-writable",
                       "publisher-signature-or-consent", "attestation-key"),
        effect_line="pack を scope に展開して lock に記録します。source が "
                    "`<source>:<pack>@<version>` のときだけ git で取りに行きます",
        effect_class="writes-worktree",
        network="sometimes",
        flags=(
            Flag(name="source", type="string",
                 help="ディレクトリ・zip・tar・`domain:`/`official:` 別名・"
                      "`<source>:<pack>@<version>`", required=True),
            _SCOPE, _ROOT,
            Flag(name="--allow-unverified", type="bool",
                 help="署名のない pack を project scope に限って許す"),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="展開して lock に記録した"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.test",
        parent="pack",
        verb="test",
        intent="measure whether this pack's prompts really do what its evaluation cases say "
               "they do, rather than taking the author's word for it",
        preconditions=("pack-dir", "approved-eval-case", "result-dir-external",
                       "judge-provider-paired", "paid-provider-consent"),
        effect_line="--provider なしなら構造の確認だけです。provider を指定すると評価ケースを"
                    "実行し、結果を --result-dir に書き出します（codex 指定時は送信します）",
        effect_class="writes-state",
        network="sometimes",
        flags=(
            Flag(name="pack", type="string", help="測る pack", required=True),
            Flag(name="--provider", type="choice", help="被験側の provider",
                 choices=("mock", "codex")),
            Flag(name="--model", type="string", help="被験側の model（--provider と対）"),
            Flag(name="--judge-provider", type="choice", help="判定側の provider",
                 choices=("mock", "codex")),
            Flag(name="--judge-model", type="string", help="判定側の model"),
            Flag(name="--command", type="string", help="provider を起動するコマンド"),
            Flag(name="--judge-command", type="string", help="判定側を起動するコマンド"),
            Flag(name="--timeout", type="float", help="1 実行あたりの制限秒数", default=30),
            Flag(name="--draft", type="string",
                 help="承認済みケースがまだない段階で、draft を pack の合成 prompt に対して測る"),
            Flag(name="--result-dir", type="path",
                 help="結果の書き出し先。pack と project の外でなければならない"),
            Flag(name="--allow-paid-provider", type="bool",
                 help="課金される provider を使うことを明示的に許可する"),
            _JSON,
        ),
        exit_codes=(
            ExitCode(code=0, meaning="構造確認だけ、または評価が通った"),
            ExitCode(code=1, meaning="評価が失敗した"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.import-results",
        parent="pack",
        verb="import-results",
        intent="bring evidence that was measured somewhere else back into the pack it "
               "vouches for",
        preconditions=("pack-dir", "result-dir-exists", "results-canonical"),
        effect_line="staged された評価結果を pack の evidence として取り込みます",
        effect_class="writes-worktree",
        network="never",
        flags=(
            Flag(name="pack", type="string", help="取り込み先の pack", required=True),
            Flag(name="--result-dir", type="path", help="取り込む結果のあるディレクトリ",
                 required=True),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="evidence を取り込んだ"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.sign",
        parent="pack",
        verb="sign",
        intent="put my publisher identity on a pack so whoever installs it can tell it "
               "really came from me and has not been altered since",
        preconditions=("pack-dir", "manifest-canonical", "signing-key", "key-id-registered"),
        effect_line="private key で pack に署名し、pack.sig.json を書き出します"
                    "（以後この pack は sync できなくなります）",
        effect_class="writes-worktree",
        network="never",
        flags=(
            Flag(name="pack", type="path", help="署名する pack", required=True),
            Flag(name="--private-key", type="path", help="署名に使う秘密鍵", required=True),
            Flag(name="--key-id", type="string", help="鍵の識別子", required=True),
            Flag(name="--signer", type="string", help="署名者の名前", required=True),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="署名して pack.sig.json を書き出した"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.keygen",
        parent="pack",
        verb="keygen",
        intent="create the publishing identity I will sign packs with, and register it "
               "where installers look for trust",
        preconditions=("key-path-free", "trust-roots-path", "key-id-free"),
        effect_line="publisher の秘密鍵を新しく生成してファイルに書き、"
                    "対応する公開鍵を trust-roots に登録します",
        effect_class="writes-worktree",
        network="never",
        flags=(
            Flag(name="--private-key", type="path", help="秘密鍵の書き出し先", required=True),
            Flag(name="--trust-roots", type="path", help="公開鍵を登録する先", required=True),
            Flag(name="--key-id", type="string", help="鍵の識別子", required=True),
            Flag(name="--signer", type="string", help="署名者の名前", required=True),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="鍵を生成し、trust-roots に登録した"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.remove",
        parent="pack",
        verb="remove",
        intent="take an installed pack back out again, without breaking whatever else here "
               "still depends on it",
        preconditions=("pack-lock", "pack-installed", "no-dependent-packs"),
        effect_line="--yes を付けたときだけ、インストール済み pack のディレクトリを削除して "
                    "lock から外します。付けなければ対象を表示するだけです",
        effect_class="writes-worktree",
        network="never",
        flags=(
            Flag(name="id", type="string", help="取り除く pack の id", required=True),
            _SCOPE, _ROOT,
            Flag(name="--yes", type="bool", help="実際に削除する（なければ dry-run）"),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="削除した、または削除対象を表示した (dry-run)"),
            _PACK_ERROR,
        ),
    ),
    Capability(
        id="pack.invoke",
        parent="pack",
        verb="invoke",
        intent="run the thing a pack ships, by name, without copying it into my own project "
               "first",
        preconditions=("pack-installed", "entrypoint-declared", "entrypoint-invokable",
                       "pack-trusted", "recipe-orchestratable"),
        effect_line="entrypoint が command なら実行計画を表示するだけです。recipe なら "
                    "orchestrator が実際に走り、worktree にも provider にも届きます",
        effect_class="writes-worktree",
        network="sometimes",
        flags=(
            Flag(name="entrypoint", type="string", help="`<pack>:<entry>`", required=True),
            Flag(name="args", type="string-list", help="entrypoint にそのまま渡す引数"),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="計画を表示した、または recipe が正常に終わった"),
            ExitCode(code=1, meaning="recipe が拒否・失敗の状態で終わった"),
            _PACK_ERROR,
        ),
    ),
)


# ── eval ─────────────────────────────────────────────────────────────────────
EVAL: tuple[Capability, ...] = (
    Capability(
        id="eval.validate",
        parent="eval",
        verb="validate",
        intent="check that my evaluation cases are well-formed and uniquely named before "
               "anything spends a provider call on them",
        preconditions=("eval-case-exists", "case-canonical-json", "case-id-unique"),
        effect_line="承認済みケースと draft を読み込んで検証し、結果を表示します"
                    "（何も書き換えません）",
        effect_class="read-only",
        network="never",
        flags=(Flag(name="path", type="path", help="1 件、またはケースの入ったディレクトリ"),),
        exit_codes=(
            ExitCode(code=0, meaning="読めたケースはすべて妥当だった"),
            _EVAL_ERROR,
        ),
    ),
    Capability(
        id="eval.list",
        parent="eval",
        verb="list",
        intent="see which cases are already approved and which are still drafts waiting for "
               "evidence",
        preconditions=("none",),
        effect_line="承認済みケースとローカルの draft を読み出して一覧します",
        effect_class="read-only",
        network="never",
        flags=(Flag(name="--repo", type="path", help="対象の repository", default="."),),
        exit_codes=(
            ExitCode(code=0, meaning="一覧した（0 件でも 0）"),
            _EVAL_ERROR,
        ),
    ),
    Capability(
        id="eval.capture",
        parent="eval",
        verb="capture",
        intent="turn something that already went wrong into the beginning of a test, so the "
               "same failure gets caught next time instead of remembered",
        preconditions=("git-repo", "task-exists", "incident-task", "draft-slot-free"),
        effect_line=".rig/evals/drafts/<task-id>/ に未承認の draft を書き出します"
                    "（赤の再現を証明したわけではありません）",
        effect_class="writes-state",
        network="never",
        flags=(
            Flag(name="task_id", type="string", help="取り込む workbench task", required=True),
            Flag(name="--repo", type="path", help="対象の repository", default="."),
            Flag(name="--allow-nonincident", type="bool",
                 help="失敗として記録されていない task からでも作る"),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="draft を書き出した"),
            _EVAL_ERROR,
        ),
    ),
    Capability(
        id="eval.run",
        parent="eval",
        verb="run",
        intent="measure how a prompt behaves right now, enough times over that one lucky "
               "answer cannot be mistaken for an improvement",
        preconditions=("eval-case-exists", "provider-available", "judge-provider-paired",
                       "repeat-matches-case"),
        effect_line="case を repeat 回実行して結果を .rig/evals/results/ に書き出します。"
                    "--provider mock はローカルだけ、claude / codex は provider に送信します",
        effect_class="writes-state",
        network="sometimes",
        flags=(
            Flag(name="case_or_suite", type="string", help="ケース id、suite 名、またはパス",
                 required=True),
            Flag(name="--provider", type="choice", help="被験側の provider",
                 choices=_PROVIDERS, required=True),
            Flag(name="--model", type="string", help="被験側の model", required=True),
            Flag(name="--repeat", type="int", help="何回まわすか（ケースの宣言と一致すること）",
                 required=True),
            Flag(name="--phase", type="choice", help="修正前を測るのか、いまを測るのか",
                 choices=("baseline", "current"), required=True),
            Flag(name="--repo", type="path", help="対象の repository", default="."),
            Flag(name="--command", type="string", help="provider を起動するコマンド"),
            Flag(name="--timeout", type="float", help="1 実行あたりの制限秒数", default=30),
            Flag(name="--judge-provider", type="choice", help="判定側の provider",
                 choices=_PROVIDERS),
            Flag(name="--judge-model", type="string", help="判定側の model"),
            Flag(name="--judge-command", type="string", help="判定側を起動するコマンド"),
            Flag(name="--judge-timeout", type="float", help="判定 1 回の制限秒数", default=30),
            Flag(name="--execution-base", type="string", help="実行時に置く base revision"),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="全ケースを実行し、結果を書き出した"),
            _EVAL_ERROR,
        ),
    ),
    Capability(
        id="eval.reproduce",
        parent="eval",
        verb="reproduce",
        intent="prove the failure I just captured really does happen on the unfixed code, "
               "before anybody is allowed to call it fixed",
        preconditions=("draft-case-exists", "provider-available", "judge-provider-paired",
                       "mock-probe-consent"),
        effect_line="draft を修正前の baseline に対して実行し、結果を .rig/evals/results/ に"
                    "書き出します。mock 以外の provider を指定すると送信します",
        effect_class="writes-state",
        network="sometimes",
        flags=(
            Flag(name="draft_id", type="string", help="再現させる draft", required=True),
            Flag(name="--provider", type="choice", help="被験側の provider",
                 choices=_PROVIDERS, required=True),
            Flag(name="--model", type="string", help="被験側の model", required=True),
            Flag(name="--repo", type="path", help="対象の repository", default="."),
            Flag(name="--command", type="string", help="provider を起動するコマンド"),
            Flag(name="--timeout", type="float", help="1 実行あたりの制限秒数", default=30),
            Flag(name="--judge-provider", type="choice", help="判定側の provider",
                 choices=_PROVIDERS),
            Flag(name="--judge-model", type="string", help="判定側の model"),
            Flag(name="--judge-command", type="string", help="判定側を起動するコマンド"),
            Flag(name="--judge-timeout", type="float", help="判定 1 回の制限秒数", default=30),
            Flag(name="--execution-base", type="string", help="実行時に置く base revision"),
            Flag(name="--allow-mock", type="bool",
                 help="mock での再現は開発用の探りでしかないと承知したうえで実行する"),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="target が赤で clean が緑、判定も測れた（再現が立った）"),
            ExitCode(code=1, meaning="再現が立たなかった、または mock の探り実行だった"),
            _EVAL_ERROR,
        ),
    ),
    Capability(
        id="eval.compare",
        parent="eval",
        verb="compare",
        intent="decide whether the change actually moved the number, or whether the two "
               "runs simply disagree with each other",
        preconditions=("baseline-result", "current-result", "results-canonical",
                       "attestation-key", "eval-case-exists"),
        effect_line="2 つの結果ファイルを読み比べ、比較レポートを表示します"
                    "（何も書き換えず、provider も呼びません）",
        effect_class="read-only",
        network="never",
        flags=(
            Flag(name="--baseline", type="path", help="修正前の結果", required=True),
            Flag(name="--current", type="path", help="いまの結果", required=True),
            Flag(name="--repo", type="path", help="対象の repository", default="."),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="pass: 赤→緑と clean 維持の条件を満たした"),
            ExitCode(code=1, meaning="条件を満たさなかった"),
            _EVAL_ERROR,
        ),
    ),
    Capability(
        id="eval.promote",
        parent="eval",
        verb="promote",
        intent="accept a draft as a permanent regression test, once the evidence says it "
               "has earned the place",
        preconditions=("draft-case-exists", "baseline-result", "current-result",
                       "attestation-key", "rubric-judged", "case-slot-free"),
        effect_line="evidence が合格していれば draft を approved にして evals/cases/ に"
                    "（--into 指定時はその pack の中に）書き出します",
        effect_class="writes-worktree",
        network="never",
        flags=(
            Flag(name="draft_id", type="string", help="昇格させる draft", required=True),
            Flag(name="--baseline", type="path", help="修正前の結果", required=True),
            Flag(name="--current", type="path", help="いまの結果", required=True),
            Flag(name="--repo", type="path", help="対象の repository", default="."),
            Flag(name="--into", type="path",
                 help="repository ではなくこの pack に書く（あとで pack sync が要る）"),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="承認済みケースとして書き出した"),
            _EVAL_ERROR,
        ),
    ),
    Capability(
        id="eval.affected",
        parent="eval",
        verb="affected",
        intent="find out which prompt behaviour this change puts at risk, and where there "
               "is no test to notice if it breaks",
        preconditions=("git-repo", "base-revision-resolvable", "surface-registry"),
        effect_line="base と head の差分を prompt surface に対応づけて報告します"
                    "（何も書き換えず、provider も呼びません）",
        effect_class="read-only",
        network="never",
        flags=(
            Flag(name="--base", type="string", help="比較元の revision", required=True),
            Flag(name="--head", type="string", help="比較先", default="working"),
            Flag(name="--repo", type="path", help="対象の repository", default="."),
            Flag(name="--require-cases", type="bool",
                 help="影響を受ける surface すべてに既存ケースを要求する（厳格）"),
            Flag(name="--ratchet", type="bool",
                 help="被覆は増やす方向のみ。ケースのない surface は debt として 0 で報告する"),
            Flag(name="--evidence-dir", type="path", help="被覆判定に使う evidence の場所"),
            _JSON,
        ),
        exit_codes=(
            ExitCode(code=0, meaning="被覆されている、または debt として報告した"),
            ExitCode(code=1, meaning="uncovered: 被覆のない surface や被覆の後退がある"),
            _EVAL_ERROR,
        ),
    ),
    Capability(
        id="eval.gate",
        parent="eval",
        verb="gate",
        intent="let CI refuse a change whose effect on prompts nobody measured, without CI "
               "itself having to spend a provider call to find out",
        preconditions=("git-repo", "base-revision-resolvable", "evidence-dir",
                       "attestation-key", "evidence-committed"),
        effect_line="コミット済みの evidence を差分に照らして検証します。"
                    "provider は呼ばず、ネットワークにも出ません",
        effect_class="read-only",
        network="never",
        flags=(
            Flag(name="--base", type="string", help="比較元の revision", required=True),
            Flag(name="--head", type="string", help="比較先", default="working"),
            Flag(name="--repo", type="path", help="対象の repository", default="."),
            Flag(name="--evidence-dir", type="path", help="検証する evidence の場所",
                 required=True),
            Flag(name="--provider", type="string",
                 help="evidence がこの provider で測られていることを要求する"),
            Flag(name="--model", type="string", help="同じく model を要求する"),
            Flag(name="--judge-provider", type="string", help="同じく判定側 provider を要求する"),
            Flag(name="--judge-model", type="string", help="同じく判定側 model を要求する"),
            Flag(name="--ratchet", type="bool",
                 help="ケースのない surface は debt 扱いにし、被覆の後退だけを致命とする"),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="影響がない、または evidence が要求を満たしている"),
            ExitCode(code=1, meaning="failed: 被覆がない・evidence が足りない・一致しない"),
            _EVAL_ERROR,
        ),
    ),
    Capability(
        id="eval.affected-run",
        parent="eval",
        verb="affected-run",
        intent="measure everything this change touches and judge it in one go, leaving "
               "evidence I can commit so nobody has to re-run it downstream",
        preconditions=("git-repo", "base-revision-resolvable", "provider-available",
                       "judge-provider-available", "evidence-dir-writable"),
        effect_line="影響を受ける case を実際に実行し、署名付き evidence を evals/evidence/ に"
                    "書き出したうえで gate まで通します（mock 以外の provider は送信します）",
        effect_class="writes-worktree",
        network="sometimes",
        flags=(
            Flag(name="--base", type="string", help="比較元の revision", required=True),
            Flag(name="--head", type="string", help="比較先", default="HEAD"),
            Flag(name="--repo", type="path", help="対象の repository", default="."),
            Flag(name="--provider", type="choice", help="被験側の provider",
                 choices=_PROVIDERS, required=True),
            Flag(name="--model", type="string", help="被験側の model", required=True),
            Flag(name="--judge-provider", type="choice", help="判定側の provider",
                 choices=_PROVIDERS, required=True),
            Flag(name="--judge-model", type="string", help="判定側の model", required=True),
            Flag(name="--command", type="string", help="provider を起動するコマンド"),
            Flag(name="--judge-command", type="string", help="判定側を起動するコマンド"),
            Flag(name="--timeout", type="float", help="1 実行あたりの制限秒数", default=30),
            Flag(name="--ratchet", type="bool",
                 help="被覆のある surface だけ測り、残りは debt として報告する"),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="実行して evidence を書き、gate も通った"),
            ExitCode(code=1, meaning="gate が通らなかった"),
            _EVAL_ERROR,
        ),
    ),
)


# ── baseline ─────────────────────────────────────────────────────────────────
BASELINE: tuple[Capability, ...] = (
    Capability(
        id="baseline.capture",
        parent="baseline",
        verb="capture",
        intent="freeze today's measured numbers as the thing every later run has to be at "
               "least as good as",
        preconditions=("benchmark-evidence-v2", "output-path-writable"),
        effect_line="benchmark の記録から baseline と scorecard を作り、"
                    "--output のファイルに書き出します",
        effect_class="writes-worktree",
        network="never",
        flags=(
            Flag(name="--input", type="path", help="benchmark schema v2 の記録", required=True),
            Flag(name="--output", type="path", help="baseline の書き出し先", required=True),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="baseline を書き出した"),
            ExitCode(code=2, meaning="BaselineError: 記録を baseline として信用できない"),
        ),
    ),
    Capability(
        id="baseline.show",
        parent="baseline",
        verb="show",
        intent="read a frozen baseline back, and see what it was measured from and when",
        preconditions=("baseline-file",),
        effect_line="baseline を読み出して表示します（何も書き換えません）",
        effect_class="read-only",
        network="never",
        flags=(
            Flag(name="baseline", type="path", help="表示する baseline", required=True),
            _JSON,
        ),
        exit_codes=(
            ExitCode(code=0, meaning="baseline を表示した"),
            ExitCode(code=2, meaning="BaselineError: baseline を読めない・信用できない"),
        ),
    ),
    Capability(
        id="baseline.compare",
        parent="baseline",
        verb="compare",
        intent="find out whether the latest run has slipped against the numbers we agreed "
               "to hold, and on exactly which identity",
        preconditions=("baseline-file", "benchmark-evidence-v2", "baseline-fresh"),
        effect_line="いまの benchmark 記録を baseline と突き合わせ、後退があれば列挙します"
                    "（何も書き換えません）",
        effect_class="read-only",
        network="never",
        flags=(
            Flag(name="--baseline", type="path", help="突き合わせる baseline", required=True),
            Flag(name="--current", type="path", help="いまの benchmark 記録", required=True),
            _JSON,
        ),
        exit_codes=(
            ExitCode(code=0, meaning="pass: 後退はない"),
            ExitCode(code=1, meaning="後退があった"),
            ExitCode(code=2, meaning="BaselineError: 記録を突き合わせられない"),
        ),
    ),
)


# ── githooks ─────────────────────────────────────────────────────────────────
GITHOOKS: tuple[Capability, ...] = (
    Capability(
        id="githooks.install",
        parent="githooks",
        verb="install",
        intent="make an ordinary git commit and git push run the same machine checks rig "
               "runs, without anyone having to remember to ask for them",
        preconditions=("git-repo", "hook-templates-available", "no-foreign-hook"),
        effect_line=".git/hooks/ に pre-commit・commit-msg・pre-push を書き込み、"
                    "いまの .claude/rig.md を信頼済みとして記録します",
        effect_class="writes-worktree",
        network="never",
        flags=(
            Flag(name="--force", type="bool", help="rig 以外の既存 hook でも上書きする"),
            Flag(name="--repo", type="path", help="対象の repository（既定は現在地）"),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="hook を入れた（または入れ直した）"),
            ExitCode(code=1, meaning="rig 以外の hook があり、--force なしなので入れなかった"),
            ExitCode(code=2, meaning="知らない引数、または知らない action"),
        ),
    ),
    Capability(
        id="githooks.uninstall",
        parent="githooks",
        verb="uninstall",
        intent="stop rig from running on my commits, without disturbing hooks I wrote "
               "myself",
        preconditions=("git-repo",),
        effect_line="rig が入れた hook だけを .git/hooks/ から削除します"
                    "（rig 以外の hook はそのまま残します）",
        effect_class="writes-worktree",
        network="never",
        flags=(Flag(name="--repo", type="path", help="対象の repository（既定は現在地）"),),
        exit_codes=(
            ExitCode(code=0, meaning="rig の hook を削除した（もともと無くても 0）"),
            ExitCode(code=1, meaning="hook ディレクトリを特定できなかった"),
            ExitCode(code=2, meaning="知らない引数、または知らない action"),
        ),
    ),
    Capability(
        id="githooks.status",
        parent="githooks",
        verb="status",
        intent="find out whether these checks are actually in place here, rather than "
               "assumed to be because somebody once ran the installer",
        preconditions=("git-repo",),
        effect_line="hook ごとの状態（rig 管理・他人のもの・無し）を読み出して表示します",
        effect_class="read-only",
        network="never",
        flags=(Flag(name="--repo", type="path", help="対象の repository（既定は現在地）"),),
        exit_codes=(
            ExitCode(code=0, meaning="rig の hook がすべて入っている"),
            ExitCode(code=1, meaning="入っていない、または rig 以外の hook がある"),
            ExitCode(code=2, meaning="知らない引数、または知らない action"),
        ),
    ),
)


#: This module's whole contribution, in the order a listing shows it.
SUBGROUP_CAPABILITIES: tuple[Capability, ...] = (
    *GOVERN,
    *PACK,
    *EVAL,
    *BASELINE,
    *GITHOOKS,
)
