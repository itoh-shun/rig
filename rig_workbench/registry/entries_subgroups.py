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

**`govern` is the only group here that writes `summary`, and all ten of its verbs do.** It is
the only group whose parser is a projection today, so it is the only one where the question
has actually been put: `add_parser(help=...)` now prints the table, and printing `intent`
rewrote all ten lines a person met in `rig-wb govern --help`. The two lines are different in
kind, verb by verb. `intent` answers 「これ、まだ承認おりてない?」 and is forbidden by its own
field docs from naming mechanism; the help column wants the shortest line that picks a verb
out of ten, and it may carry operational detail no conversation needs — `can`'s shipped line
ends "(exit 0 allowed / 3 denied)", which is the entire reason CI calls it, and `migrate`'s
names the two v1 files it folds, which is how somebody recognises their own situation. So the
ten shipped lines are declared here as `summary` and the ten `intent` lines are left alone.
The other groups declare no `summary`: their parsers are still hand-written, and writing one
now would be guessing at a divergence nobody has met. When a group migrates, its verbs face
this question one at a time, and a verb whose one line honestly serves both readers writes
nothing — `model.Capability` refuses a `summary` that merely repeats the `intent`.

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
    help="which installation tier to read (org needs RIG_ORG_HOME)",
    choices=("project", "user", "org"),
    default="project",
)
_ROOT = Flag(name="--root", type="path",
             help="use this directory instead of the scope's default root")
_JSON = Flag(name="--json", type="bool", help="machine-readable output")

_PROVIDERS = ("mock", "claude", "codex", "command")


# ── govern ───────────────────────────────────────────────────────────────────
GOVERN: tuple[Capability, ...] = (
    Capability(
        id="govern.init",
        parent="govern",
        verb="init",
        intent="start holding this repository to a shared standard, with something to edit "
               "rather than a blank page",
        summary="bind this repository to an org/team and scaffold a starter policy",
        preconditions=("git-repo", "org-binding-absent"),
        effect_line=".rig/org.json と starter policy (.rig/policy/org.json) を書き出し、"
                    "監査台帳に policy.init を追記します",
        effect_class="writes-state",
        network="never",
        flags=(
            Flag(name="--org", type="string", help="org identifier (e.g. acme)", required=True),
            Flag(name="--team", type="string", help="team identifier (e.g. team-a)"),
            Flag(name="--layer", type="string-list",
                 help="path to an existing policy layer, repeatable and applied in order (relative "
                      "paths also resolve against $RIG_POLICY_HOME)"),
            Flag(name="--force", type="bool", help="overwrite existing files"),
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
        summary="fold v1 .rig/access.json / .rig/gates.json into a policy layer",
        preconditions=("git-repo", "legacy-access-or-gates-file", "org-known"),
        effect_line="v1 の .rig/access.json と .rig/gates.json を畳んだ policy 文書を "
                    ".rig/policy/<id>.json に書き出します（元のファイルはそのまま動きます）",
        effect_class="writes-state",
        network="never",
        flags=(
            Flag(name="--org", type="string",
                 help="org identifier (defaults to the one in .rig/org.json)"),
            Flag(name="--scope", type="choice", help="the scope this layer applies at",
                 choices=("org", "team", "project"), default="project"),
            Flag(name="--team", type="string", help="team identifier (required with --scope team)"),
            Flag(name="--id", type="string",
                 help="policy document id (default: migrated)", default="migrated"),
            Flag(name="--out", type="path", help="write here instead of .rig/policy/<id>.json"),
            Flag(name="--force", type="bool", help="overwrite an existing file"),
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
        summary="show or lint the policy in effect",
        preconditions=("git-repo", "policy-layers-resolvable"),
        effect_line="有効な policy を読み出して表示します。lint も層を検証するだけで、"
                    "何も書き換えません",
        effect_class="read-only",
        network="never",
        flags=(
            Flag(name="action", type="choice", help="show the policy in effect, or lint the layers",
                 choices=("show", "lint"), default="show"),
            Flag(name="paths", type="string-list",
                 help="with lint: specific documents (default: the resolved layers)"),
            Flag(name="--json", type="bool", help="with show: machine-readable output"),
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
        summary="the roles and permissions of the current actor",
        preconditions=("git-repo", "policy-layers-resolvable", "actor-identity"),
        effect_line="現在の actor の role と permission を読み出して表示します",
        effect_class="read-only",
        network="never",
        flags=(Flag(name="--actor", type="string", help="ask about somebody else"),),
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
        summary="check a single permission (exit 0 allowed / 3 denied)",
        preconditions=("git-repo", "policy-layers-resolvable", "actor-identity",
                       "known-permission"),
        effect_line="permission を 1 件だけ判定して結果を表示します（何も書き換えません）",
        effect_class="read-only",
        network="never",
        flags=(
            Flag(name="permission", type="string", help="the permission to check",
                 required=True),
            Flag(name="--actor", type="string", help="ask about somebody else"),
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
        summary="grant/deny an approval, or show a task's approval status",
        preconditions=("git-repo", "task-exists", "actor-identity", "permission-approve"),
        effect_line="grant と deny は承認の決定を記録し、監査台帳にも追記します。"
                    "status は読み出すだけです",
        effect_class="writes-state",
        network="never",
        flags=(
            Flag(name="action", type="choice", help="show the approval status, or grant or deny it",
                 choices=("status", "grant", "deny"), default="status"),
            Flag(name="task_id", type="string", help="defaults to the most recent task"),
            Flag(name="--note", type="string",
                 help="why (recorded with the decision; required in practice for deny)"),
            Flag(name="--actor", type="string", help="record the decision under this identity"),
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
        summary="grant, list or revoke time-boxed exceptions",
        preconditions=("git-repo", "policy-layers-resolvable", "actor-identity",
                       "permission-waiver", "waiver-criterion-named"),
        effect_line="grant は .rig/waivers.json に免除を追記し、revoke はそれを失効させます"
                    "（どちらも監査台帳に残ります）。list は読み出すだけです",
        effect_class="writes-state",
        network="never",
        flags=(
            Flag(name="action", type="choice", help="list the waivers, or grant or revoke one",
                 choices=("list", "grant", "revoke"), default="list"),
            Flag(name="id", type="string", help="waiver id (with grant/revoke)"),
            # `--criterion` collects into `args.criteria`: the flag is repeatable, so what
            # it builds is a list, and `cmd_waiver` reads it under that name.
            Flag(name="--criterion", type="string-list", dest="criteria",
                 help="gate criterion this waiver excuses (repeatable)"),
            Flag(name="--reason", type="string", help="why this exception exists"),
            Flag(name="--expires", type="string",
                 help="YYYY-MM-DD (defaults to the policy's maximum)"),
            Flag(name="--scope", type="string",
                 help="fnmatch pattern over task_type or task_id (default: *)", default="*"),
            Flag(name="--actor", type="string", help="act as this identity"),
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
        summary="read, verify or export the tamper-evident ledger",
        preconditions=("git-repo", "audit-ledger", "permission-audit-export"),
        effect_line="log と verify は台帳を読むだけです。export は台帳を書き出したうえで、"
                    "その書き出し自体を台帳に追記します",
        effect_class="writes-state",
        network="never",
        flags=(
            Flag(name="action", type="choice",
                 help="read the ledger, verify its chain, or export it",
                 choices=("log", "verify", "export"), default="log"),
            Flag(name="--limit", type="int", help="with log: show only the latest N entries"),
            # `audit` already has an `action` positional (log / verify / export), so this
            # option cannot take the dest argparse would derive: it would overwrite the word
            # the person typed. `dest="filter_action"` is what the shipped parser declares,
            # and `Capability` now refuses the collision rather than leaving it to be found
            # by parsing `audit verify --action policy.init` and losing `verify`.
            Flag(name="--action", type="string", dest="filter_action",
                 help="filter by action name"),
            Flag(name="--since", type="string", help="only entries since YYYY-MM-DD"),
            Flag(name="--format", type="choice", help="with export: output format",
                 choices=("jsonl", "csv", "markdown"), default="jsonl"),
            Flag(name="--out", type="path", help="with export: write to this file"),
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
        summary="measure this repository against its effective policy",
        preconditions=("git-repo", "org-binding", "policy-layers-resolvable"),
        effect_line="この repository を有効な policy に照らして採点し、"
                    "検査ごとの結果を表示します（何も書き換えません）",
        effect_class="read-only",
        network="never",
        flags=(
            Flag(name="path", type="path", help="repository to measure (default: the current one)"),
            Flag(name="--since-days", type="int",
                 help="run window for the measured checks", default=90),
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
        summary="aggregate several projects into the org/team view",
        preconditions=("project-paths-exist", "org-binding"),
        effect_line="指定した repository をそれぞれローカルに読み取り、org / team 単位に"
                    "集計して表示します（ネットワークには出ません）",
        effect_class="read-only",
        network="never",
        flags=(
            Flag(name="paths", type="string-list",
                 help="repository paths (or directories with --scan)", required=True),
            Flag(name="--scan", type="bool",
                 help="treat each path as a directory whose immediate children are repositories"),
            Flag(name="--since-days", type="int",
                 help="run window for the measured checks", default=90),
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
            Flag(name="id", type="string", help="the pack id", required=True),
            Flag(name="--kind", type="choice", help="which tier this pack is for",
                 choices=("core", "official", "domain", "project"), default="project"),
            Flag(name="--type", type="choice",
                 help="what the pack contains and may run",
                 choices=("knowledge", "policy", "reviewer", "skill", "workflow", "tool"),
                 required=True),
            Flag(name="--root", type="path",
                 help="directory to create the pack under", default=".rig/packs"),
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
            Flag(name="path", type="path",
                 help="the pack to validate (default: the current directory)"),
            Flag(name="--global", type="bool",
                 help="validate every installed tier instead of one pack"),
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
            Flag(name="path", type="path", help="diagnose just this pack"),
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
        preconditions=("pack-dir", "pack-not-installed", "every-file-in-an-asset-dir"),
        effect_line="pack.yaml の assets と hashes をディスクの実体から作り直して"
                    "上書きします（asset ディレクトリの外にあるファイルは拒否します。"
                    "pack.lock.json が所有する導入済み pack も、lock が固定した "
                    "manifest を書き換えることになるため拒否します）",
        effect_class="writes-worktree",
        network="never",
        flags=(Flag(name="path", type="path",
                    help="the pack to sync (default: the current directory)"),),
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
            Flag(name="name", type="string",
                 help="the name this source is referred to by", required=True),
            Flag(name="--scheme", type="choice", help="how the source is fetched",
                 choices=("git+ssh", "git+https", "git+file"), required=True),
            Flag(name="--url", type="string", help="URL template containing {pack}",
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
        flags=(Flag(name="name", type="string",
                    help="the name of the source to remove", required=True),),
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
            Flag(name="path", type="path", help="the pack to export", required=True),
            Flag(name="--to", type="path", help="directory to export into", required=True),
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
            Flag(name="path", type="path", help="the pack to bundle", required=True),
            Flag(name="--to", type="path", help="output zip (default: dist/<id>-<version>.zip)"),
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
        intent="see what is installed here, where each one came from, and what its own "
               "evaluation evidence was found to support at install time",
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
            Flag(name="pack", type="string", help="the id of the pack to inspect", required=True),
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
            Flag(name="pack", type="string", help="the id of the pack to inspect", required=True),
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
                 help="repeatable; a pack matches if it declares any of them"),
            Flag(name="--scope", type="string-list",
                 help="repeatable; a bare dimension (`product`) matches every value under it, a "
                      "valued one (`product:x`) is exact"),
            Flag(name="--scope-filter", type="choice", help="which installation tier to read",
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
            Flag(name="pack", type="string", help="the id of the pack to move", required=True),
            Flag(name="--to", type="string", help="the version to move to", required=True),
            _SCOPE, _ROOT,
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
                       "attestation-key"),
        effect_line="pack を scope に展開して lock に記録します。source が "
                    "`<source>:<pack>@<version>` のときだけ git で取りに行きます",
        effect_class="writes-worktree",
        network="sometimes",
        flags=(
            Flag(name="source", type="string",
                 help="a directory, a zip, a tar, a `domain:`/`official:` alias, or "
                      "`<source>:<pack>@<version>`", required=True),
            _SCOPE, _ROOT,
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
            Flag(name="pack", type="string", help="the pack to measure", required=True),
            Flag(name="--provider", type="choice", help="the provider under test",
                 choices=("mock", "codex")),
            Flag(name="--model", type="string",
                 help="the model under test (paired with --provider)"),
            Flag(name="--judge-provider", type="choice", help="the provider that judges",
                 choices=("mock", "codex")),
            Flag(name="--judge-model", type="string", help="the model that judges"),
            Flag(name="--command", type="string", help="command that starts the provider"),
            Flag(name="--judge-command", type="string", help="command that starts the judge"),
            Flag(name="--timeout", type="float", help="seconds allowed per run", default=30),
            Flag(name="--draft", type="string",
                 help="measure a draft case from .rig/evals/drafts/ against this pack's composed "
                      "prompt, before the pack has an approved case; the evidence it writes is "
                      "what `eval promote --into` then needs"),
            Flag(name="--result-dir", type="path",
                 help="where results are written; must be outside the pack and the project"),
            Flag(name="--allow-paid-provider", type="bool",
                 help="explicitly allow a provider that bills"),
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
            Flag(name="pack", type="string", help="the pack to import into", required=True),
            Flag(name="--result-dir", type="path", help="directory holding the results to import",
                 required=True),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="evidence を取り込んだ"),
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
            Flag(name="id", type="string", help="the id of the pack to remove", required=True),
            _SCOPE, _ROOT,
            Flag(name="--yes", type="bool", help="actually delete; without it this is a dry run"),
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
            Flag(name="args", type="string-list",
                 help="arguments passed straight through to the entrypoint"),
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
        flags=(Flag(name="path", type="path", help="one case, or a directory of cases"),),
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
        flags=(Flag(name="--repo", type="path", help="the repository to act on", default="."),),
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
            Flag(name="task_id", type="string",
                 help="the workbench task to capture", required=True),
            Flag(name="--repo", type="path", help="the repository to act on", default="."),
            Flag(name="--allow-nonincident", type="bool",
                 help="capture even from a task not recorded as a failure"),
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
            Flag(name="case_or_suite", type="string", help="a case id, a suite name, or a path",
                 required=True),
            Flag(name="--provider", type="choice", help="the provider under test",
                 choices=_PROVIDERS, required=True),
            Flag(name="--model", type="string", help="the model under test", required=True),
            Flag(name="--repeat", type="int",
                 help="how many runs; must match what the case declares",
                 required=True),
            Flag(name="--phase", type="choice",
                 help="measure the pre-fix baseline, or the current state",
                 choices=("baseline", "current"), required=True),
            Flag(name="--repo", type="path", help="the repository to act on", default="."),
            Flag(name="--command", type="string", help="command that starts the provider"),
            Flag(name="--timeout", type="float", help="seconds allowed per run", default=30),
            Flag(name="--judge-provider", type="choice", help="the provider that judges",
                 choices=_PROVIDERS),
            Flag(name="--judge-model", type="string", help="the model that judges"),
            Flag(name="--judge-command", type="string", help="command that starts the judge"),
            Flag(name="--judge-timeout", type="float",
                 help="seconds allowed per judgement", default=30),
            Flag(name="--execution-base", type="string", help="base revision to run against"),
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
            Flag(name="draft_id", type="string", help="the draft to reproduce", required=True),
            Flag(name="--provider", type="choice", help="the provider under test",
                 choices=_PROVIDERS, required=True),
            Flag(name="--model", type="string", help="the model under test", required=True),
            Flag(name="--repo", type="path", help="the repository to act on", default="."),
            Flag(name="--command", type="string", help="command that starts the provider"),
            Flag(name="--timeout", type="float", help="seconds allowed per run", default=30),
            Flag(name="--judge-provider", type="choice", help="the provider that judges",
                 choices=_PROVIDERS),
            Flag(name="--judge-model", type="string", help="the model that judges"),
            Flag(name="--judge-command", type="string", help="command that starts the judge"),
            Flag(name="--judge-timeout", type="float",
                 help="seconds allowed per judgement", default=30),
            Flag(name="--execution-base", type="string", help="base revision to run against"),
            Flag(name="--allow-mock", type="bool",
                 help="reproduce with mock, knowing it only probes the harness"),
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
            Flag(name="--baseline", type="path", help="the pre-fix results", required=True),
            Flag(name="--current", type="path", help="the current results", required=True),
            Flag(name="--repo", type="path", help="the repository to act on", default="."),
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
            Flag(name="draft_id", type="string", help="the draft to promote", required=True),
            Flag(name="--baseline", type="path", help="the pre-fix results", required=True),
            Flag(name="--current", type="path", help="the current results", required=True),
            Flag(name="--repo", type="path", help="the repository to act on", default="."),
            Flag(name="--into", type="path",
                 help="write the approved case into this pack instead of the repository; run "
                      "`rig-wb pack sync` afterwards to declare it"),
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
            Flag(name="--base", type="string", help="the revision to compare from", required=True),
            Flag(name="--head", type="string",
                 help="the revision to compare to", default="working"),
            Flag(name="--repo", type="path", help="the repository to act on", default="."),
            Flag(name="--require-cases", type="bool",
                 help="every affected surface must already have a case (strict)"),
            Flag(name="--ratchet", type="bool",
                 help="coverage may only go up: a surface with no case yet is reported as debt "
                      "(exit 0), removing existing coverage fails"),
            Flag(name="--evidence-dir", type="path",
                 help="where the evidence used for the coverage check lives"),
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
            Flag(name="--base", type="string", help="the revision to compare from", required=True),
            Flag(name="--head", type="string",
                 help="the revision to compare to", default="working"),
            Flag(name="--repo", type="path", help="the repository to act on", default="."),
            Flag(name="--evidence-dir", type="path", help="where the evidence to check lives",
                 required=True),
            Flag(name="--provider", type="string",
                 help="require the evidence to have been measured with this provider"),
            Flag(name="--model", type="string",
                 help="require the evidence to have been measured with this model"),
            Flag(name="--judge-provider", type="string",
                 help="require the evidence to have been judged by this provider"),
            Flag(name="--judge-model", type="string",
                 help="require the evidence to have been judged by this model"),
            Flag(name="--ratchet", type="bool",
                 help="coverage may only go up: an affected surface with no case yet is debt "
                      "rather than a failure, while removing coverage and unregistered surface "
                      "kinds stay fatal"),
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
            Flag(name="--base", type="string", help="the revision to compare from", required=True),
            Flag(name="--head", type="string", help="the revision to compare to", default="HEAD"),
            Flag(name="--repo", type="path", help="the repository to act on", default="."),
            Flag(name="--provider", type="choice", help="the provider under test",
                 choices=_PROVIDERS, required=True),
            Flag(name="--model", type="string", help="the model under test", required=True),
            Flag(name="--judge-provider", type="choice", help="the provider that judges",
                 choices=_PROVIDERS, required=True),
            Flag(name="--judge-model", type="string", help="the model that judges", required=True),
            Flag(name="--command", type="string", help="command that starts the provider"),
            Flag(name="--judge-command", type="string", help="command that starts the judge"),
            Flag(name="--timeout", type="float", help="seconds allowed per run", default=30),
            Flag(name="--ratchet", type="bool",
                 help="measure the covered surfaces and report the rest as debt, instead of "
                      "refusing to measure anything while one affected surface has no case yet"),
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
            Flag(name="--input", type="path",
                 help="the benchmark schema v2 record to read", required=True),
            Flag(name="--output", type="path", help="where to write the baseline", required=True),
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
            Flag(name="baseline", type="path", help="the baseline to show", required=True),
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
            Flag(name="--baseline", type="path",
                 help="the baseline to compare against", required=True),
            Flag(name="--current", type="path", help="the current benchmark record", required=True),
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
            Flag(name="--force", type="bool", help="overwrite a hook that is already there"),
            Flag(name="--repo", type="path",
                 help="the repository to act on instead of the current directory"),
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
        flags=(Flag(name="--repo", type="path",
                    help="the repository to act on instead of the current directory"),),
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
        flags=(Flag(name="--repo", type="path",
                    help="the repository to act on instead of the current directory"),),
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
