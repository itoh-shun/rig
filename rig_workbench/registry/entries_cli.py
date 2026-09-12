"""The verbs `rig-wb <verb>` dispatches at the top level, declared once.

`rig_workbench/cli.py:main` reaches these two ways and nothing else: an explicit
`if sub == "..."` branch (twenty of them, `version` through `validate`), and membership of
`_orch_delegates`, a set handed straight to `rig_workbench/orchestrate/cli.py`'s `COMMANDS`
(eighteen names, of which `validate` is also an explicit branch and the explicit branch wins,
so seventeen of them are only reachable that way). Twenty plus seventeen is the thirty-seven
declared below, and the arithmetic is the derivation: no third path exists, and a verb that
is in neither place prints `Unknown sub-command` and exits 2.

**A few of the thirty-seven are dispatchable and advertised nowhere, on purpose.** What
`rig-wb --help` lists is frozen as `TOP_LEVEL_SUBCOMMANDS` in
`tests/test_cli_surface_contract.py`; what it leaves out is named verb by verb, each with
its reason, as `TOP_LEVEL_VERBS_MISSING_FROM_HELP` in
`tests/test_capability_registry_vs_cli.py`. Those two literals are where the counts live,
and both are asserted against the real process. Restating either number here would put a
third copy of one fact in a third file, which is the arrangement this module exists to end.
Every verb declared below is in one list or the other, and one in neither fails that second
test rather than passing quietly.

Thirteen sat in the second list until they were audited one verb at a time. Nine moved into
the first: for each, some document or test already handed a person the `rig-wb <verb>`
spelling, so the omission was in the help text alone. None was dropped — every one of the
thirteen had a document or a test behind it, unlike `list` and `review` below. The rest are
reached through another rig surface under another spelling, and their entries stay here at
full weight regardless: a verb a person can type is a capability whether or not help
mentions it, and the reason for the silence belongs beside the silence rather than in place
of the declaration.

They were fifteen until `list` and `review` were taken out of `_orch_delegates`. Those two
were worse than undocumented: the set had them, orchestrate's `COMMANDS` never did, so each
fell through to `main`'s `sys.argv[1] not in COMMANDS` branch and answered with the
orchestrator's module docstring and exit 1 — measured, not inferred. Rather than write an
`intent` that promised a listing a moment before rig did nothing, the verbs and their two
entries are gone. `rig-wb wb review` records a per-persona verdict and is a different verb
under a different parent; it is untouched.

Declaration only, per `model.Capability`: no handler is imported and no callable is stored.
The `output_schema` of every entry here is `None`, which is a finding and not an oversight —
cross-checked against `tests/test_schema_registry.py`'s frozen set, not one top-level verb
emits an id from it. `plan --json` and `fleet --json` print bare JSON documents with no
`schema` key, and `rig.fleet/v1` (`evidence.py`) names the *configuration* fleet reads, not
what it prints. The enveloped ids all belong to `wb` and `govern` sub-verbs, which are other
entry modules' to declare.

`exit_codes` prefer what `tests/test_exit_code_surface.py` observed through real processes
over what a docstring claims: `gh-check`'s 3 and 5, `ja-lint`'s and `design-constraints`'
1-versus-2 split, `bench`'s 1 for a completed non-pass, and the orchestrator's 3 for a run
parked on a human gate are all measured numbers.
"""

from __future__ import annotations

from .model import (
    NETWORK_ALWAYS,
    NETWORK_NEVER,
    NETWORK_SOMETIMES,
    NO_PRECONDITION,
    READ_ONLY,
    WRITES_STATE,
    WRITES_WORKTREE,
    Capability,
    ExitCode,
    Flag,
)

# ── exit codes that recur ────────────────────────────────────────────────────
# rig's published three (exitcodes.py): 0 the answer is yes, 1 rig judged and said no,
# 2 rig could not produce an answer. Spelled out per entry rather than shared as a tuple
# so that an entry which really only ever returns 0 cannot claim a rejection it never makes.
_OK = ExitCode(code=0, meaning="ran, and the answer is yes")
_REJECTED = ExitCode(code=1, meaning="rig looked and the answer is no")
_ERROR = ExitCode(code=2, meaning="rig could not produce an answer: bad usage or unreadable state")


CLI_CAPABILITIES: tuple[Capability, ...] = (
    # ── the twenty explicit `sub == "..."` branches, in dispatch order ───────
    Capability(
        id="version",
        parent=None,
        verb="version",
        intent="find out which rig-wb this is, usually to check it against what a doc assumes",
        preconditions=(NO_PRECONDITION,),
        effect_line="インストールされている rig-wb のバージョンを表示します",
        effect_class=READ_ONLY,
        network=NETWORK_NEVER,
        exit_codes=(_OK,),
    ),
    Capability(
        id="usage",
        parent=None,
        verb="usage",
        intent="see how much of my work actually went through rig rather than around it",
        preconditions=("runs-log",),
        effect_line=".rig/runs.jsonl（--global なら ~/.rig/runs.jsonl）を読んで集計を表示します",
        effect_class=READ_ONLY,
        network=NETWORK_NEVER,
        flags=(
            Flag(name="--limit", type="int", help="how many recent records to count"),
            Flag(name="--global", type="bool", help="read ~/.rig/runs.jsonl across every project"),
            Flag(name="--json", type="bool", help="emit the tally as JSON"),
        ),
        exit_codes=(_OK,),
    ),
    Capability(
        id="bench",
        parent=None,
        verb="bench",
        intent="get evidence on whether driving the work through rig beats doing it bare",
        preconditions=("rig-home", "corpus-available", "provider-available", "paid-provider-optin"),
        effect_line=(
            "corpus の各タスクを bare 側と rig 側で走らせます。"
            "--provider mock はローカルのみ、claude/codex は provider に送信します"
        ),
        effect_class=WRITES_STATE,
        network=NETWORK_SOMETIMES,
        flags=(
            Flag(name="--corpus", type="path", help="an external task corpus instead of the packaged one"),
            Flag(name="--tasks", type="string-list", help="task ids to run, or `all`"),
            Flag(name="--provider", type="string", help="the provider both arms drive", default="mock"),
            Flag(name="--model", type="string", help="the model both arms drive unless an arm overrides it"),
            Flag(name="--bare-model", type="string", help="the model for the bare arm only"),
            Flag(name="--rig-model", type="string", help="the model for the rig arm only"),
            Flag(name="--runs", type="int", help="planned pairs per task", default=1),
            Flag(name="--max-steps", type="int", help="stop each rig-arm run after this many steps", default=14),
            Flag(name="--provider-timeout", type="float", help="seconds allowed per provider call", default=600.0),
            Flag(name="--rig-timeout", type="float", help="seconds allowed for a whole rig-arm run", default=1800.0),
            Flag(name="--check-timeout", type="float", help="seconds allowed for a task's own check command", default=60.0),
            Flag(name="--base-url", type="string", help="the endpoint an HTTP provider is called at"),
            Flag(name="--allow-headless-in-cc", type="bool", help="allow a headless CLI provider inside Claude Code"),
            Flag(name="--caller", type="string", help="name the harness that invoked rig instead of letting it be guessed"),
            Flag(name="--mock-scenario", type="choice", help="which outcome the mock provider acts out",
                 choices=("success", "timeout", "malformed", "partial"), default="success"),
            Flag(name="--allow-paid-provider", type="bool", help="explicit opt-in for claude/codex"),
            Flag(name="--out", type="path", help="write the schema-v2 report here"),
            Flag(name="--html", type="path", help="write an HTML rendering of the report here"),
            Flag(name="--runs-log", type="path", help="append every rig-arm run's runs.jsonl record here as well"),
        ),
        exit_codes=(
            _OK,
            ExitCode(code=1, meaning="the benchmark completed without reaching a pass"),
            ExitCode(code=2, meaning="a CLI or schema error, including a paid provider without the opt-in"),
        ),
    ),
    Capability(
        id="baseline",
        parent=None,
        verb="baseline",
        intent="pin what today's numbers look like so a later run can be judged against them",
        preconditions=("git-repo", "rig-home"),
        effect_line="ベンチマークの baseline を記録・比較・表示します（capture は既存の baseline を上書きします）",
        effect_class=WRITES_STATE,
        network=NETWORK_NEVER,
        flags=(Flag(name="verb", type="string", help="capture, compare or show"),),
        exit_codes=(_OK, _REJECTED, _ERROR),
    ),
    Capability(
        id="eval",
        parent=None,
        verb="eval",
        intent="stop a fix that already worked once from quietly breaking again later",
        preconditions=("git-repo", "rig-home", "provider-available"),
        effect_line=(
            "回帰評価ケースを操作します。run/capture は provider に送信し、"
            "list/validate/compare はローカルだけで完結します"
        ),
        effect_class=WRITES_WORKTREE,
        network=NETWORK_SOMETIMES,
        flags=(Flag(name="verb", type="string", help="validate, list, capture, run, compare, promote, …"),),
        exit_codes=(_OK, _REJECTED, _ERROR),
    ),
    Capability(
        id="pack",
        parent=None,
        verb="pack",
        intent="get someone else's reviewers and recipes into this project, or hand mine out",
        preconditions=("rig-home",),
        effect_line=(
            "prompt pack を操作します。install はパッケージ同梱のカタログのみ、"
            "source/sync は宣言された git リモートに接続します"
        ),
        effect_class=WRITES_WORKTREE,
        network=NETWORK_SOMETIMES,
        flags=(Flag(name="verb", type="string", help="init, validate, install, sign, invoke, sync, …"),),
        exit_codes=(_OK, _REJECTED, _ERROR),
    ),
    Capability(
        id="govern",
        parent=None,
        verb="govern",
        intent="make the team's rules about who may approve what into something rig enforces",
        preconditions=("git-repo", "governance-bound"),
        effect_line="組織ポリシー・権限・承認・waiver・監査台帳を操作します（判断は .rig 配下に記録されます）",
        effect_class=WRITES_STATE,
        network=NETWORK_NEVER,
        flags=(Flag(name="verb", type="string", help="init, policy, whoami, can, approve, waiver, audit, …"),),
        exit_codes=(
            _OK,
            _REJECTED,
            _ERROR,
            ExitCode(code=3, meaning="the actor does not hold the permission asked about"),
        ),
    ),
    Capability(
        id="asvs",
        parent=None,
        verb="asvs",
        intent="find out which classes of security defect this project has no way of noticing",
        preconditions=("rig-home", "asvs-map-exists"),
        effect_line="ASVS の各章に対して rig が持つ検査手段を突き合わせ、空欄を表示します",
        effect_class=READ_ONLY,
        network=NETWORK_NEVER,
        flags=(
            Flag(name="--map", type="path", help="an ASVS map other than the packaged one"),
            Flag(name="--check", type="bool", help="fail instead of reporting when a chapter has no mechanism"),
            Flag(name="--json", type="bool", help="emit the map as JSON"),
        ),
        exit_codes=(_OK, _REJECTED, _ERROR),
    ),
    Capability(
        id="coverage",
        parent=None,
        verb="coverage",
        intent="check that every documented promise still has something behind it that proves it",
        preconditions=("rig-home", "evidence-map-exists"),
        effect_line=(
            "要件と evidence の対応表を検証します。"
            "--run を付けると宣言された evidence コマンドをこの場で実行します"
        ),
        effect_class=READ_ONLY,
        network=NETWORK_NEVER,
        flags=(
            Flag(name="--map", type="path", help="a coverage map other than the packaged one"),
            Flag(name="--run", type="bool", help="execute the deterministic evidence, not just verify the map"),
            Flag(name="--only", type="string", help="restrict --run to one requirement id"),
            Flag(name="--markdown", type="bool", help="emit the map as a Markdown table"),
            Flag(name="--json", type="bool", help="emit the map as JSON"),
        ),
        exit_codes=(_OK, _REJECTED, _ERROR),
    ),
    Capability(
        id="design-constraints",
        parent=None,
        verb="design-constraints",
        intent="catch a mockup that drifted off the project's own tokens and wording before anyone builds it",
        preconditions=("constraints-declared", "artifact-paths-given"),
        effect_line="設計成果物を宣言済みのトークン・コンポーネント・禁止語と照合します（--report 指定時のみ書き出します）",
        effect_class=WRITES_STATE,
        network=NETWORK_NEVER,
        flags=(
            Flag(name="artifact", type="string-list", help="the design artefacts to check"),
            Flag(name="--constraints", type="path", help="the declaration to check against"),
            Flag(name="--report", type="path", help="write the findings here"),
            Flag(name="--if-configured", type="bool", help="pass quietly when the project declared nothing"),
            Flag(name="--json", type="bool", help="emit the findings as JSON"),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="checked clean, or the project declared nothing to check"),
            ExitCode(code=1, meaning="violations were found"),
            ExitCode(code=2, meaning="unchecked: a declaration exists but could not be read"),
        ),
    ),
    Capability(
        id="ja-lint",
        parent=None,
        verb="ja-lint",
        intent="have the Japanese in a document read like one person wrote it, before it goes out",
        preconditions=("ja-lint-config",),
        effect_line=(
            "日本語の文章を文長・助詞・ら抜き・文体混在などで検査します。"
            "--report は所見を書き出し、--fix は対象ファイル本文を書き換えます"
        ),
        # `--fix` writes back into the document being checked (ja_textlint.py), so this is a
        # worktree writer and not a state writer. It was declared `writes-state` while the
        # flag that does the writing was undeclared; declaring the flag settles the class.
        effect_class=WRITES_WORKTREE,
        network=NETWORK_NEVER,
        flags=(
            Flag(name="artifact", type="string-list", help="files to lint; `-` reads stdin, none uses the config's paths"),
            Flag(name="--config", type="path", help="the lint configuration to use"),
            Flag(name="--report", type="path", help="write the findings here"),
            Flag(name="--preset", type="string", help="the rule preset to apply"),
            Flag(name="--rule", type="string-list", help="run only the rule of this name, repeatable"),
            Flag(name="--strict", type="bool", help="treat warnings as errors"),
            Flag(name="--fix", type="bool", help="apply the mechanically fixable findings to the files and report the rest"),
            Flag(name="--staged", type="bool", help="check only the Japanese prose the git index adds"),
            Flag(name="--changed", type="string", help="check only the Japanese prose added since this base ref"),
            Flag(name="--if-configured", type="bool", help="pass quietly when nothing is configured"),
            Flag(name="--list-rules", type="bool", help="print every rule and its default severity, then stop"),
            Flag(name="--json", type="bool", help="emit the findings as JSON"),
        ),
        exit_codes=(
            ExitCode(code=0, meaning="clean, or nothing was configured to check"),
            ExitCode(code=1, meaning="errors were found in the prose"),
            ExitCode(code=2, meaning="unchecked: the configuration is broken"),
        ),
    ),
    Capability(
        id="hostcheck",
        parent=None,
        verb="hostcheck",
        intent="know whether this machine is set up so rig's safety assumptions actually hold",
        preconditions=("git-repo",),
        effect_line="rig 自身では強制できないホスト側の前提（隔離・permissions.deny・ignore 設定）を点検します",
        effect_class=READ_ONLY,
        network=NETWORK_NEVER,
        flags=(
            Flag(name="--repo", type="path", help="the repository root to inspect", default="."),
            Flag(name="--strict", type="bool", help="exit 1 instead of 3 when a prerequisite is missing"),
            Flag(name="--bench", type="bool", help="measure the host probes instead of reporting them"),
            Flag(name="--json", type="bool", help="emit the full result as JSON"),
        ),
        exit_codes=(
            _OK,
            ExitCode(code=1, meaning="a prerequisite is missing and --strict was asked for"),
            ExitCode(code=3, meaning="a prerequisite is missing"),
        ),
    ),
    Capability(
        id="mutation",
        parent=None,
        verb="mutation",
        intent="find out whether the tests would actually notice if the code started being wrong",
        preconditions=("git-repo", "mutation-report-present"),
        effect_line=(
            "Stryker/mutmut のレポートを採点します。"
            "--run はプロジェクトのツールを実行し、--record-baseline は baseline を上書きします"
        ),
        effect_class=WRITES_STATE,
        network=NETWORK_NEVER,
        flags=(
            Flag(name="format", type="string", help="the report format, when it cannot be detected"),
            Flag(name="report", type="path", help="the report to score"),
            Flag(name="--repo", type="path", help="the project root to inspect", default="."),
            # Both of these double the positional above them, so neither can take the dest
            # argparse would derive — it would overwrite the positional. `mutation.py` says
            # `dest="report_flag"` / `dest="format_flag"` for that reason, and the same
            # collision check that caught `govern audit --action` caught this declaration
            # claiming otherwise.
            Flag(name="--report", type="path", dest="report_flag",
                 help="the report to read, as a flag rather than the positional"),
            Flag(name="--format", type="choice", dest="format_flag",
                 help="force the report format instead of reading it from the file",
                 choices=("elements", "junit", "mutmut")),
            Flag(name="--run", type="bool", help="run the project's own mutation tool first"),
            Flag(name="--baseline", type="path", help="the baseline to compare against"),
            Flag(name="--tolerance", type="float", help="how far the score may drop before it is actionable"),
            Flag(name="--record-baseline", type="bool", help="record this score as the new baseline"),
            Flag(name="--apply", type="string", help="attach the score to this task's gate"),
            Flag(name="--json", type="bool", help="emit the score as JSON"),
        ),
        exit_codes=(_OK, _REJECTED, _ERROR),
    ),
    Capability(
        id="sensor-bench",
        parent=None,
        verb="sensor-bench",
        intent="measure how much the deterministic checks catch on their own, with no model involved",
        preconditions=("rig-home",),
        effect_line="secrets/injection/destructive の機械センサーを同梱コーパスで採点します（LLM も課金もありません）",
        effect_class=READ_ONLY,
        network=NETWORK_NEVER,
        flags=(Flag(name="--json", type="bool", help="emit the full result as JSON"),),
        exit_codes=(_OK,),
    ),
    Capability(
        id="bench-invariance",
        parent=None,
        verb="bench-invariance",
        intent="check that the benchmark says the same thing twice, so its numbers can be argued from",
        preconditions=("rig-home", "corpus-available", "provider-available", "paid-provider-optin"),
        effect_line=(
            "同じタスクを繰り返し走らせて結果の一致率を測ります。"
            "--provider mock はローカルのみ、claude/codex は provider に送信します"
        ),
        effect_class=WRITES_STATE,
        network=NETWORK_SOMETIMES,
        flags=(
            Flag(name="--corpus", type="path", help="an external task corpus instead of the packaged one"),
            Flag(name="--tasks", type="string-list", help="task ids to run, or `all`"),
            Flag(name="--provider", type="string", help="the provider the whole panel is driven through", default="mock"),
            Flag(name="--models", type="string", help="the comma-separated model panel to measure across", required=True),
            Flag(name="--runs", type="int", help="repetitions per task", default=1),
            Flag(name="--agreement-threshold", type="float", help="the agreement rate that counts as invariant", default=0.8),
            Flag(name="--max-steps", type="int", help="stop each run after this many steps", default=14),
            Flag(name="--provider-timeout", type="float", help="seconds allowed per provider call", default=600.0),
            Flag(name="--rig-timeout", type="float", help="seconds allowed for a whole run", default=1800.0),
            Flag(name="--check-timeout", type="float", help="seconds allowed for a task's own check command", default=60.0),
            Flag(name="--base-url", type="string", help="the endpoint an HTTP provider is called at"),
            Flag(name="--allow-headless-in-cc", type="bool", help="allow a headless CLI provider inside Claude Code"),
            Flag(name="--caller", type="string", help="name the harness that invoked rig instead of letting it be guessed"),
            Flag(name="--mock-scenario", type="choice", help="which outcome the mock provider acts out",
                 choices=("success", "timeout", "malformed", "partial"), default="success"),
            Flag(name="--allow-paid-provider", type="bool", help="explicit opt-in for claude/codex"),
            Flag(name="--out", type="path", help="write the report here"),
            Flag(name="--html", type="path", help="write an HTML rendering of the report here"),
        ),
        exit_codes=(_OK, _REJECTED, _ERROR),
    ),
    Capability(
        id="gh-check",
        parent=None,
        verb="gh-check",
        intent="find out whether the GitHub helpers are usable here before relying on one",
        preconditions=(NO_PRECONDITION,),
        effect_line="`gh` と github/gh-stack の状態を github.com に問い合わせて報告します（何も書き換えません）",
        effect_class=READ_ONLY,
        network=NETWORK_ALWAYS,
        flags=(Flag(name="--json", type="bool", help="emit the full state as JSON"),),
        exit_codes=(
            ExitCode(code=0, meaning="gh and the gh-stack extension are both present"),
            ExitCode(code=2, meaning="an unknown flag was passed"),
            ExitCode(code=3, meaning="gh is not on PATH at all"),
            ExitCode(code=5, meaning="gh is there without the gh-stack extension"),
        ),
    ),
    Capability(
        id="githooks",
        parent=None,
        verb="githooks",
        intent="have the deterministic checks run on commit without anyone remembering to run them",
        preconditions=("git-repo", "hooks-dir-writable"),
        effect_line="このリポジトリの .git/hooks に pre-commit/pre-push を設置・削除・確認します",
        effect_class=WRITES_WORKTREE,
        network=NETWORK_NEVER,
        flags=(
            Flag(name="verb", type="string", help="install, uninstall or status"),
            Flag(name="--repo", type="path", help="the repository to act on instead of the current directory"),
            Flag(name="--force", type="bool", help="overwrite a hook that is already there"),
        ),
        exit_codes=(_OK, _REJECTED, _ERROR),
    ),
    Capability(
        id="wb",
        parent=None,
        verb="wb",
        intent="work on one task end to end — start it, check it, and decide whether to keep it",
        preconditions=("git-repo", "rig-home"),
        effect_line=(
            "workbench のタスク操作に入ります。accept/discard は worktree と branch を書き換え、"
            "review 系は provider に送信することがあります"
        ),
        effect_class=WRITES_WORKTREE,
        network=NETWORK_SOMETIMES,
        flags=(Flag(name="verb", type="string", help="new, step, gate, accept, discard, board, audit, stats, …"),),
        exit_codes=(_OK, _REJECTED, _ERROR),
    ),
    Capability(
        id="dashboard",
        parent=None,
        verb="dashboard",
        intent="see how the last stretch of work went on one page I can show someone",
        preconditions=("git-repo", "runs-log"),
        effect_line=".rig/runs.jsonl から単一ファイルの HTML を組み立てます（--out 指定時のみ書き出し、既定は標準出力）",
        effect_class=WRITES_STATE,
        network=NETWORK_NEVER,
        flags=(
            Flag(name="--repo", type="path", help="the repository root to read"),
            Flag(name="--out", type="path", help="write the HTML here instead of to stdout"),
            Flag(name="--limit", type="int", help="how many recent runs to show", default=20),
            Flag(name="--recipe", type="string", help="only runs of this recipe"),
            Flag(name="--since", type="string", help="only runs at or after YYYY-MM-DD"),
        ),
        exit_codes=(_OK, _ERROR),
    ),
    Capability(
        id="validate",
        parent=None,
        verb="validate",
        intent="check that rig's own recipes, personas and commands still hold together before shipping them",
        preconditions=("rig-home", "pyyaml-installed"),
        effect_line="同梱の recipe・persona・command・skill を検査して PASS/WARN/FAIL を集計します（読み取りだけです）",
        effect_class=READ_ONLY,
        network=NETWORK_NEVER,
        flags=(Flag(name="mode", type="string", help="`selftest` to run the golden self-verification instead"),),
        exit_codes=(
            ExitCode(code=0, meaning="no FAIL, though there may be WARNs to address"),
            ExitCode(code=1, meaning="at least one check failed"),
            ExitCode(code=2, meaning="an unknown flag; nothing was read and nothing checked"),
        ),
    ),
    # ── the nineteen `_orch_delegates` reachable only through orchestrate ────
    Capability(
        id="fleet",
        parent=None,
        verb="fleet",
        intent="compare how the same reviewers are doing across several projects at once",
        preconditions=("repos-selected",),
        effect_line="指定した各リポジトリの .rig/runs.jsonl と drill-results.jsonl を読んで集計します（どこにも書き込みません）",
        effect_class=READ_ONLY,
        network=NETWORK_NEVER,
        flags=(
            Flag(name="--repos", type="string", help="comma-separated repository paths to compare"),
            Flag(name="--discovered", type="bool", help="use every project ~/.rig/runs.jsonl has recorded"),
            Flag(name="--anonymize", type="bool", help="label repositories repo-1, repo-2, … instead of by path"),
            Flag(name="--json", type="bool", help="emit the rollup as JSON"),
        ),
        exit_codes=(_OK, ExitCode(code=1, meaning="no repository list, or nothing to report")),
    ),
    Capability(
        id="run",
        parent=None,
        verb="run",
        intent="hand a whole job to rig and let it drive the steps until it is done or it stops",
        preconditions=(
            "git-repo", "rig-home", "pyyaml-installed", "recipe-exists", "recipe-trusted",
            "recipe-executable", "provider-named", "provider-available", "goal-provided",
        ),
        effect_line=(
            "recipe の各ステップを実行します。--isolate は worktree と branch を作り、"
            "--provider mock はローカルのみ、それ以外は goal と diff を provider に送信します"
        ),
        effect_class=WRITES_WORKTREE,
        network=NETWORK_SOMETIMES,
        # In `cmd_run`'s own parse order (rig_workbench/orchestrate/commands.py), so that the
        # two can be read side by side. Everything that loop accepts is here: the entry used
        # to declare fourteen of the thirty-eight, which is how `--model` came to be passed
        # by action.yml, parsed by cmd_run, and declared nowhere.
        flags=(
            Flag(name="recipe", type="string", help="the recipe to run", required=True),
            Flag(name="--provider", type="string", help="the generator provider", required=True),
            Flag(name="--generators", type="string", help="comma-separated generator providers to spread the steps across"),
            Flag(name="--verifier-provider", type="string", help="the provider that verifies"),
            Flag(name="--verifier-providers", type="string", help="comma-separated providers for a mixed-model quorum"),
            Flag(name="--provider-cmd", type="string", help="an explicit command template for the `cmd` provider"),
            # The pin flags below are the argv spelling of the same file --secure-provider-config
            # holds; a recipe declaring secure-provider-execution needs one or the other.
            Flag(name="--secure-provider-config", type="path", help="a JSON file pinning each role's executable and digest"),
            Flag(name="--generator-executable", type="path", help="the generator binary the run is pinned to"),
            Flag(name="--generator-executable-sha256", type="string", help="the digest that generator binary must have"),
            Flag(name="--generator-interpreter", type="path", help="the interpreter that generator binary runs under"),
            Flag(name="--generator-interpreter-sha256", type="string", help="the digest that interpreter must have"),
            Flag(name="--verifier-executable", type="path", help="the verifier binary the run is pinned to"),
            Flag(name="--verifier-executable-sha256", type="string", help="the digest that verifier binary must have"),
            Flag(name="--verifier-interpreter", type="path", help="the interpreter that verifier binary runs under"),
            Flag(name="--verifier-interpreter-sha256", type="string", help="the digest that interpreter must have"),
            Flag(name="--model", type="string", help="the model every step asks for unless something more specific wins"),
            Flag(name="--step-model", type="string-list", help="one `<step-id>=<model>` override, repeatable"),
            Flag(name="--base-url", type="string", help="the endpoint an HTTP provider is called at"),
            Flag(name="--timeout", type="int", help="seconds allowed per provider call", default=600),
            # Two spellings of one switch: `cmd_run` accepts `--auto-model-setting` as well,
            # and a `Flag` has no field for an alias. Named in the help rather than declared
            # twice, which would read as two independent switches.
            Flag(name="--auto-model", type="bool",
                 help="take the model from what `models --save` cached (also spelled --auto-model-setting)"),
            Flag(name="--goal", type="string", help="the goal, given in argv"),
            Flag(name="--goal-stdin", type="bool", help="read the goal from stdin instead of argv"),
            Flag(name="--review-category", type="choice", help="required by the secure Japanese-writing recipes",
                 choices=("general", "incident_report", "support_reply")),
            Flag(name="--material-profile", type="choice", help="optional style material for secure Japanese writing",
                 choices=("none", "technical", "conversation"), default="none"),
            Flag(name="--check", type="string-list", help="one shell check to append to the `checks-only` steps, repeatable"),
            Flag(name="--out", type="path", help="where the run-state is written", default="run-state.json"),
            Flag(name="--max-steps", type="int", help="stop after this many steps", default=40),
            Flag(name="--max-parallel", type="int", help="how many verifiers run at once", default=4),
            # `string`, not `choice`: `cmd_run` stores whatever is given and only `majority`
            # is ever tested for, so a typo silently means `all` rather than being refused.
            # Declaring choices here would promise a validation the code does not perform.
            Flag(name="--quorum", type="string", help="`majority` to accept on a majority of verifier votes; anything else means all", default="all"),
            Flag(name="--isolate", type="bool", help="run in a disposable git worktree, ff-merging only on green"),
            Flag(name="--allow-headless-in-cc", type="bool", help="allow a headless CLI provider inside Claude Code"),
            Flag(name="--no-session-persistence", type="bool", help="stop the claude provider persisting its session"),
            Flag(name="--auto-route", type="bool", help="pick the cheapest candidate model that covers the diff size"),
            Flag(name="--reuse-session", type="bool", help="let the generator CLI carry its conversation across steps"),
            Flag(name="--auto-route-learn", type="bool", help="route from what runs.jsonl records rather than size alone"),
            Flag(name="--auto-route-mode", type="choice", help="whether a learned route is only recorded or actually applied",
                 choices=("shadow", "active"), default="shadow"),
            Flag(name="--exploration-pct", type="int", help="the percentage of runs that try the next-cheapest candidate"),
            Flag(name="--exploration-date", type="string", help="the date bucket the exploration hash is taken from"),
        ),
        exit_codes=(
            _OK,
            ExitCode(code=1, meaning="the run failed or escalated"),
            _ERROR,
            ExitCode(code=3, meaning="the run parked on a human gate; neither a pass nor a failure"),
        ),
    ),
    Capability(
        id="plan",
        parent=None,
        verb="plan",
        intent="see what rig would do before letting it do any of it",
        preconditions=("rig-home", "pyyaml-installed", "recipe-exists", "recipe-trusted"),
        effect_line="recipe のステップと分岐を計算して表示します（実行はせず、何も書き込みません）",
        effect_class=READ_ONLY,
        network=NETWORK_NEVER,
        flags=(
            Flag(name="recipe", type="string", help="the recipe to resolve", required=True),
            Flag(name="--json", type="bool", help="emit the resolved plan as JSON"),
            Flag(name="--with", type="string", help="the flags to resolve the plan under"),
            Flag(name="--diff-lines", type="int", help="the diff size to classify against"),
            Flag(name="--diff-git", type="bool", help="measure the diff size from `git diff HEAD` instead"),
        ),
        exit_codes=(_OK, _REJECTED, _ERROR),
    ),
    Capability(
        id="runs",
        parent=None,
        verb="runs",
        intent="look back at what rig has been running here and how often it worked",
        preconditions=("git-repo", "runs-log"),
        effect_line=".rig/runs.jsonl を読んで一覧と集計を表示します（--html を付けたときだけ HTML を書き出します）",
        effect_class=WRITES_STATE,
        network=NETWORK_NEVER,
        flags=(
            Flag(name="--limit", type="int", help="how many recent runs to list", default=10),
            Flag(name="--recipe", type="string", help="only runs of this recipe"),
            Flag(name="--personas", type="bool", help="tally votes per verifier"),
            Flag(name="--cost", type="bool", help="roll up token usage per recipe and provider"),
            Flag(name="--auto-route-regret", type="bool", help="report how each routed candidate model actually fared"),
            Flag(name="--html", type="path", help="write an HTML dashboard here instead"),
            Flag(name="--since", type="string", help="only runs at or after YYYY-MM-DD"),
        ),
        exit_codes=(_OK, ExitCode(code=1, meaning="the HTML dashboard could not be produced")),
    ),
    Capability(
        id="init",
        parent=None,
        verb="init",
        intent="start a job I will drive step by step myself rather than in one go",
        preconditions=(
            "git-repo", "rig-home", "pyyaml-installed", "recipe-exists", "recipe-trusted",
            "recipe-executable",
        ),
        effect_line="recipe から run-state.json を作成し、最初のアクションを表示します（既存ファイルは上書きされます）",
        effect_class=WRITES_STATE,
        network=NETWORK_NEVER,
        flags=(
            Flag(name="recipe", type="string", help="the recipe to start", required=True),
            Flag(name="--goal", type="string", help="the goal this run is for"),
            Flag(name="--out", type="path", help="where to write the run-state", default="run-state.json"),
        ),
        exit_codes=(_OK, _REJECTED, _ERROR),
    ),
    Capability(
        id="check",
        parent=None,
        verb="check",
        intent="run the machine checks for the step I am on and record what they said",
        preconditions=("run-state-exists", "run-state-unblocked", "step-running"),
        effect_line="いま実行中のステップが宣言したチェックコマンドを実行し、結果を run-state に記録します",
        effect_class=WRITES_STATE,
        network=NETWORK_NEVER,
        flags=(Flag(name="state_json", type="path", help="the run-state to check against"),),
        exit_codes=(_OK, ExitCode(code=1, meaning="there is no running step to check"), _ERROR),
    ),
    Capability(
        id="verdict",
        parent=None,
        verb="verdict",
        intent="record an independent judgement on the step, with an answer for every criterion",
        preconditions=("run-state-exists", "run-state-unblocked", "step-running"),
        effect_line="判定者名と各受け入れ基準の答えを run-state に記録します",
        effect_class=WRITES_STATE,
        network=NETWORK_NEVER,
        flags=(
            Flag(name="state_json", type="path", help="the run-state to record into"),
            Flag(name="--by", type="string", help="who is judging", required=True),
            Flag(name="--pass", type="bool", help="the step passed"),
            Flag(name="--fail", type="bool", help="the step failed"),
            Flag(name="--criterion", type="string-list", help="one `N=PASS|FAIL|UNKNOWN` answer, repeatable"),
            Flag(name="--note", type="string", help="a note recorded with the judgement"),
        ),
        exit_codes=(_OK, ExitCode(code=1, meaning="no running step, or a criterion was left unanswered"), _ERROR),
    ),
    Capability(
        id="queue",
        parent=None,
        verb="queue",
        intent="line several jobs up and have rig work through them without me babysitting each one",
        preconditions=("git-repo", "rig-home", "queue-backend-configured", "provider-available"),
        effect_line=(
            "キューを操作します。add/list/done は記録だけ、go は各タスクを実際に走らせて "
            "worktree を作り provider に送信します"
        ),
        effect_class=WRITES_WORKTREE,
        network=NETWORK_SOMETIMES,
        flags=(
            Flag(name="verb", type="string", help="add, list, go, done, retry or cancel", required=True),
            Flag(name="--backend", type="choice", help="where the queue lives",
                 choices=("local", "github", "gitlab"), default="local"),
            Flag(name="--repo", type="string", help="owner/repo, for the github and gitlab backends"),
            Flag(name="--provider", type="string", help="the generator provider `go` drives", default="rig"),
            Flag(name="--verifier-provider", type="string", help="the provider that verifies"),
            Flag(name="--provider-cmd", type="string", help="an explicit command template for the `cmd` provider"),
            Flag(name="--max-parallel", type="int", help="how many tasks `go` runs at once", default=3),
            Flag(name="--depends-on", type="string-list", help="a task this one waits for, repeatable"),
            Flag(name="--dependency-policy", type="choice", help="what a dependency has to have reached before this task may run",
                 choices=("accepted",), default="accepted"),
        ),
        exit_codes=(_OK, _REJECTED, _ERROR),
    ),
    Capability(
        id="selftest",
        parent=None,
        verb="selftest",
        intent="prove the runner still makes the same decisions it did, before trusting a result from it",
        preconditions=("rig-home", "pyyaml-installed"),
        effect_line="決定性のゴールデン検証を実行します（テレメトリは一時ファイルに逃がされ、このリポジトリは変わりません）",
        effect_class=READ_ONLY,
        network=NETWORK_NEVER,
        exit_codes=(_OK, ExitCode(code=1, meaning="a transition no longer matches its golden")),
    ),
    Capability(
        id="graph",
        parent=None,
        verb="graph",
        intent="see how the pieces of rig here actually reference each other, not how a doc says they do",
        preconditions=("rig-home", "pyyaml-installed"),
        effect_line="同梱ブリックの frontmatter から型付きグラフを導出して表示します（手書きの図ではありません）",
        effect_class=READ_ONLY,
        network=NETWORK_NEVER,
        flags=(
            Flag(name="--json", type="bool", help="emit the whole graph as JSON"),
            Flag(name="--focus", type="string", help="show only one node's edges, in and out"),
        ),
        exit_codes=(_OK, ExitCode(code=1, meaning="no node matches the focus that was asked for")),
    ),
    Capability(
        id="models",
        parent=None,
        verb="models",
        intent="find out which models I can actually drive from this machine right now",
        preconditions=(NO_PRECONDITION,),
        effect_line=(
            "provider を探索して一覧します。既定では localhost のみに問い合わせ、"
            "--base-url を渡すとその宛先に接続します。--save は ~/.claude/rig/models.json を上書きします"
        ),
        effect_class=WRITES_STATE,
        network=NETWORK_SOMETIMES,
        flags=(
            Flag(name="--save", type="bool", help="cache what was found for the next --auto-model run"),
            Flag(name="--base-url", type="string", help="ask this endpoint instead of the local default"),
            Flag(name="--json", type="bool", help="emit the discovery as JSON"),
        ),
        exit_codes=(_OK,),
    ),
    Capability(
        id="probe",
        parent=None,
        verb="probe",
        intent="find out why a provider keeps coming back unusable, by seeing one real exchange with it",
        preconditions=("provider-named", "provider-available"),
        effect_line=(
            "provider を1回だけ呼び、実際のコマンド・出力・契約の解析結果を表示します。"
            "--provider mock 以外は外部に送信します"
        ),
        effect_class=READ_ONLY,
        network=NETWORK_SOMETIMES,
        flags=(
            Flag(name="--provider", type="string", help="the provider to call once", required=True),
            Flag(name="--role", type="choice", help="which role's prompt to send",
                 choices=("verifier", "generator"), default="verifier"),
            Flag(name="--model", type="string", help="the model to ask for"),
            Flag(name="--base-url", type="string", help="the endpoint to call"),
            Flag(name="--provider-cmd", type="string", help="an explicit command for the `cmd` provider"),
        ),
        exit_codes=(
            _OK,
            ExitCode(code=1, meaning="no provider was named, or its answer did not parse"),
        ),
    ),
    Capability(
        id="install-shim",
        parent=None,
        verb="install-shim",
        intent="be able to type `rig` from any directory instead of the long form every time",
        preconditions=("rig-home", "shim-source-present", "shim-target-writable"),
        effect_line="~/.local/bin/rig（または --to のパス）に symlink を張ります。--force は既存のファイルを削除します",
        effect_class=WRITES_WORKTREE,
        network=NETWORK_NEVER,
        flags=(
            Flag(name="--to", type="path", help="where to place the symlink", default="~/.local/bin/rig"),
            Flag(name="--force", type="bool", help="remove whatever is already at that path"),
        ),
        exit_codes=(
            _OK,
            ExitCode(code=1, meaning="the shim source is missing, or the target exists without --force"),
        ),
    ),
    Capability(
        id="otel",
        parent=None,
        verb="otel",
        intent="get rig's runs into the same observability tooling the rest of the system reports to",
        preconditions=("runs-log", "otlp-endpoint-configured"),
        effect_line=(
            "記録済みの run を OTLP に射影して送信します。--dry-run は送信せず内容だけ表示し、"
            "endpoint 未設定なら何も送りません"
        ),
        effect_class=READ_ONLY,
        network=NETWORK_SOMETIMES,
        flags=(
            Flag(name="--endpoint", type="string", help="the OTLP/HTTP endpoint to send to"),
            Flag(name="--dry-run", type="bool", help="print what would leave the machine instead of sending it"),
            Flag(name="--recipe", type="string", help="only runs of this recipe"),
            Flag(name="--limit", type="int", help="how many recent runs to project", default=50),
            Flag(name="--service-name", type="string", help="the service name to report under"),
            Flag(name="--traces-only", type="bool", help="send traces and no metrics"),
            Flag(name="--metrics-only", type="bool", help="send metrics and no traces"),
        ),
        exit_codes=(_OK, ExitCode(code=1, meaning="the export failed"), _ERROR),
    ),
    Capability(
        id="perf",
        parent=None,
        verb="perf",
        intent="find out where the time is going, and whether it has quietly got worse",
        preconditions=("runs-log",),
        effect_line=(
            "記録済みの run を phase ごとに集計します。--save-baseline は baseline ファイルを書き出し、"
            "--check は許容幅を超えたら失敗させます"
        ),
        effect_class=WRITES_STATE,
        network=NETWORK_NEVER,
        flags=(
            Flag(name="--recipe", type="string", help="only runs of this recipe"),
            Flag(name="--limit", type="int", help="how many recent runs to aggregate", default=20),
            Flag(name="--check", type="bool", help="fail when a phase grew past the tolerance"),
            Flag(name="--baseline", type="path", help="the baseline to compare against"),
            Flag(name="--save-baseline", type="path", help="record the current shape here"),
            Flag(name="--budget", type="path", help="the declared budget to judge against"),
            Flag(name="--tolerance-pct", type="float", help="how much growth is allowed", default=20.0),
        ),
        exit_codes=(
            _OK,
            ExitCode(code=1, meaning="a phase grew past the tolerance, or a declared budget was broken"),
        ),
    ),
    Capability(
        id="approve",
        parent=None,
        verb="approve",
        intent="give — or refuse — the human sign-off a run has stopped and is waiting on",
        preconditions=("run-state-exists", "run-state-unblocked", "governance-bound", "actor-identified"),
        effect_line="人手ゲートの判断を run-state と改竄検知付きの監査台帳の両方に記録します",
        effect_class=WRITES_STATE,
        network=NETWORK_NEVER,
        flags=(
            Flag(name="step_id", type="string", help="the step being decided", required=True),
            Flag(name="state_json", type="path", help="the run-state holding that step"),
            Flag(name="--deny", type="bool", help="refuse instead of approving"),
            Flag(name="--note", type="string", help="the reason, recorded with the decision"),
            Flag(name="--actor", type="string", help="decide as this actor instead of the ambient one"),
        ),
        exit_codes=(_OK, ExitCode(code=1, meaning="the decision was refused: quorum, role or freshness"),
                    _ERROR,
                    ExitCode(code=3, meaning="the decision was recorded and the run is still parked: "
                                             "quorum unmet, or denied")),
    ),
    Capability(
        id="next",
        parent=None,
        verb="next",
        intent="move the job on to whatever comes next, and be told if it cannot",
        preconditions=("run-state-exists", "run-state-unblocked"),
        effect_line="次の遷移を計算して run-state に適用し、何が起きたかを1行で表示します",
        effect_class=WRITES_STATE,
        network=NETWORK_NEVER,
        flags=(Flag(name="state_json", type="path", help="the run-state to advance"),),
        exit_codes=(
            _OK,
            ExitCode(code=1, meaning="the run escalated and needs a person"),
            ExitCode(code=2, meaning="the run is blocked and will refuse to load again"),
            ExitCode(code=3, meaning="the run parked on a human gate; neither a pass nor a failure"),
        ),
    ),
)
