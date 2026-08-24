# Changelog

## Unreleased

One malformed task record no longer takes down every reader of the runs directory.
`read_all_tasks` parsed every `.rig/runs/*/task.json` and returned a list, so a single
unparseable file raised before any caller could report anything: Mission Control produced no
page at all rather than a page missing one row.

**Skipping it would have been the other half of the same bug.** A task absent from a board
reads as a task that does not exist, and a total of 52 where 55 records exist has answered a
question nobody asked. So a directory that yields no usable record is *named*, and the count
travels with the records: `read_all_tasks` returns `TaskRecords`, which is deliberately not a
list. Iterating it raises, so no reader can take the total without the shortfall — the board,
the cockpit, the digest, Mission Control and its assurance section all render the same
sentence because they call the same method.

**A record is usable when the readers can read it, not when it is JSON.** `board` and the
cockpit index `status`, `input` and `task_type`; `stats` parses `created_at` as a date;
`load_reviews`, `gate_status_counts` and the assurance section join `task_id` onto a path. A
record that parses without one of those moved the crash a layer down instead of removing it,
so the fields are declared in one place and a record missing any of them is named like any
other. Presence is not the whole rule: `created_at` is parsed, not read as text, and a naive
timestamp raises on comparison with an aware one just as surely as an unparseable string
does, so a value that is merely non-empty is not enough. Optional fields are checked the same
way, in the one direction that matters: absent is ordinary — every reader guards them — while
present with a type no reader can use is not. `board` formats `recipe` to a width and compares
`budget_minutes` against elapsed minutes; the server sorts `updated_at` against a string. A record whose `task_id` does not name its own directory is named too: that value is
what every reader joins onto the runs directory, so `run-a` claiming to be `run-b` sends them
all to another task's artefacts, and `..` sends them out of the runs directory entirely.

**And failing to look is not finding nothing.** An absent runs directory is a cold start; a
directory that cannot be listed is reported as such, because answering "0 tasks" there would
be rig reporting on something it never read.

`board`, `log` and Mission Control's assurance section each enumerated the runs directory
inline — the last precisely because `read_all_tasks` used to raise. All three are gone: one list, one
rule for what counts as a task, and no way for two views of the same runs directory to
disagree. The shortfall reaches the rendered page and the live UI as one preformatted note
carried in the snapshot, so neither can word it differently or leave it out, and `log --json`
carries it as a field because a consumer parsing that list is exactly the caller who cannot
see a printed note. And no reader calls the runs directory empty in the same breath as naming
what is in it: "no readable tasks" is what was found, "`.rig/runs/` is empty" is a claim about
the directory, and only one of them is true at a time (#488).

SKILL.md §2's brick catalog now lists the assurance surface. `receipt`, `import` and
`contract` shipped in #428 and #429 and the run graph in #426, and README's feature table has
carried them since — but §2, which is what a session reads to find out what rig has, named
none of them. Nor did it name the eight subcommands added since: `intent`, `intent-derive`,
`assurance-target`, `assurance-derive`, `synthesise`, `dev-loop`, `route-team`, `budget-plan`
and `provenance`. Listing only the four #470 was filed about would have left the same hole for
the other eight the day it merged, so all of them are here, in three rows: the receipt and the
BYOO contract, the intent contract and the assurance target it derives, and the planning
commands that restore a floor, stop a loop, route by evidence, plan a budget and walk a chain.

This is the third time a shipped surface has gone missing from §2 (#395, #470), and there is
still nothing that would catch the fourth: `check_catalog_drift` scans four brick directories
and never the workbench CLI. A check for it is not this change — filed separately, because the
obvious version does not work. `import` and `contract` already appeared in §2 as substrings of
`/rig:import` and "output-contract facet", so a check that asked whether a name is *mentioned*
would have reported this gap as covered.

`--validate` now catches a subcommand that was implemented and never wired up. Five issues —
#261, #262, #327, #417, #473 — were the same omission found one at a time after the fact: a
user-facing `workbench.py` subcommand missing from the route table `/rig:go` dispatches from,
so the command existed and could not be reached. `check_workbench_routing` compares what the built parser
dispatches against both surfaces that make a subcommand reachable — `commands/go.md`'s route
table and the ops instruction's own list — and reports what is in neither. It found twelve on
its first run: `gates`, `receipt`, `import` and `contract` (#473), and the eight subcommands
added while wiring #432/#434/#435 into place. All twelve are wired now.

**Two surfaces, one rule.** #473 was the same four names missing from both, so a check covering
one of them would have reported that issue half-fixed.

**An allowlist is checked in the other direction too.** The subcommands `/rig:go` never routes
— the flow calls them itself — are declared rather than inferred, and an entry naming something
that is no longer a subcommand is reported: it suppresses nothing, and hides that it stopped
applying.

**It reads argparse, not the source.** What a subcommand is called in `cli.py` is one
spelling of it; what `/rig:go` can reach is what argparse will match an argv against. The check
asks the built parser, so a registration moved into a helper or a loop still counts — a regex
over the source would have lost it, and a subcommand lost silently is indistinguishable from
one that is wired.

**It reads each list where that list lives.** `go.md` explains the natural-language path below
its route table and the ops instruction documents every subcommand in its body, so a check
reading either document whole would count a name written in a sentence as a name the flow
dispatches — success reported for exactly what the check exists to catch. The route table is
found by its own header row and read to the first line that is not a row, so a second table
added beside it is not mistaken for dispatch wiring; the ops list is sliced to the header that
holds it. Every landmark must locate its list *unambiguously* — be a whole line, appear
exactly once, and come in order — or the check fails rather than reading whichever copy came
first: `> | 先頭語 | 委譲先 |` contains the header row and is a blockquote, and a test
asserting the shipped files have unique landmarks would only cover a run that executes that
test, while `--validate` could still pass having read the wrong table. And a row routes the
name its first cell *opens* with, as a backticked run that closes: `` | （廃止: `receipt`） |
`` annotates a subcommand rather than routing one, and `` | `receipt | `` is a row nobody
proof-read.

**And a check that found nothing to check has not passed.** No subcommands from the parser, no
section where the list should be, no rows in it: each is the shape this check reads having
moved, and reporting zero omissions then would be it saying "all clear" about text it never
looked at. It fails instead, and reports nothing else while it is blind. The same discipline is
why the check is a function over documents rather than a walk of the repository — it can be
shown a wiring it must object to: a name mentioned in a row's *description* rather than routed
by it, a name in a column no dispatcher reads, a table that lives outside the route-table
section (#478, #473).


An assurance target now reaches the two places that can use it. `workbench.py assurance-derive
<target> --requires <mapping> --against <catalog>` derives the workflow floor a target needs,
and the receipt — and Mission Control after it — reports what was asked for beside what was
recorded.

**The mapping from an outcome to a step is passed in.** A target names outcomes: a tree the
work was written to, a signature that verifies, a gate that passed. A workflow names steps. The
claim that *this step is how you get that outcome* is a policy, and it is declared by the
caller for the same reason `synthesise`'s floor is built by the caller and never read from the
proposal it is checking.

**An axis nobody said how to reach is refused, not skipped.** The whole failure this could
produce is a workflow that looks like it satisfies a target while nothing in it does. Silence
about `provenance: signed-and-verified` means nobody wrote down which step signs — and reading
it as "no step is needed" would put the target's own guarantee below the floor while reporting
that the floor held. An *empty* declaration is kept: "reaching this needs no step of its own"
is a thing a policy can truthfully say, and somebody wrote it. Absence is the shape that means
nobody has decided.

**And it is keyed on the axis and the value together.** `gate: skipped` is not a weaker
`gate: passed` and does not inherit its steps. `assurance-derive` also names, before it is
asked, every axis-value pair a mapping could not plan for — otherwise an operator learns about
a gap only when they happen to ask for that value.

**The receipt evaluates once, and every other view copies it.** `assurance_target.evaluate` is
called from one place; the Markdown page and Mission Control read that block rather than the
files. A dashboard disagreeing with the receipt about whether an assurance held is worse than
either being wrong alone, and eight review rounds on #476 were that defect found one layer at a
time. A target is read through the reader that writes it, so a key named twice is refused the
same way wherever it arrives — `"gate": "failed", "gate": "passed"` was previously read by
`assurance-target` as a request for a passing gate.

**`unobservable` stays its own outcome all the way to the page.** `unmet` says rig looked and
what it found falls short; `unobservable` says it cannot look. They are counted separately, get
separate tiles, and the axis rig cannot answer is never marked as a shortfall. Mission Control
puts no rate or score on those counts: a number computed by its own rule would be a second
verdict on a page whose whole claim is that it holds none. A task whose state cannot be read is
named rather than dropped: the section enumerates run directories rather than parsing every
`task.json` first, because a row missing from a dashboard reads as a task with nothing to
report. (The rest of the page still fails on a malformed `task.json` — `read_all_tasks` raises,
and what a malformed record should mean to the board is a decision of its own.)

**And nothing a file says can become something the page says.** The receipt's Markdown page
interpolated values read off disk directly, so a newline in a goal, an axis name or a
validation message ended the line the renderer wrote and started one the document's author did
— forging a heading, a table row, or a verdict. The renderer reaches an escaping
function at each site, because prose and a code span need different treatment and no single
pass over the receipt can know which one a value is heading for. That makes the rule a
distributed one — the shape eight rounds of #476 were about — so what stops it being a
*remembered* one is a test rather than a claim: the page is rendered twice from the same
receipt, once with every string replaced by a harmless value and once with every string
replaced by an adversarial one, and the two must have the same structure. Every rendered leaf,
whichever file it came from, whether or not anybody thought of it, including one added
tomorrow. It found the footer's timestamp, which nothing else had.

A mapping's *keys* are not covered by that, because most of them are names the renderer looks
up rather than words it prints — but the gate's status counts are keyed on what the acceptance
record recorded, so one of them was a disk value nothing was escaping. A key is not safer than
a value for having been used as one. The guard for the next one parses the renderer and refuses
any key it prints straight out of a mapping it iterated.

Both of those checks are themselves checked. Seven review rounds on this change found the same
thing five times — not a rule somebody forgot, but *a check that was not looking where it said
it was looking*: an escape set assembled from remembered attacks, a fixture poisoning the files
somebody picked, an oracle whose lookbehind could not tell `\*` from `\\*`, a call-site count
that matched its own comments, and a key guard that matched no loop at all and reported success
for six rounds. So the key guard is shown six unsafe shapes it must object to and four safe ones
it must not, the poisoner is required to have changed the page before its comparison means
anything, and the mutation sweep runs against the tests as well as the code. A check nothing
exercises is a check nobody knows still works.

One check was deleted rather than fixed. It read the source and refused a second caller of
`assurance_target.evaluate`, and it was broken four rounds running — it matched the spelling,
then missed an alias, then the module that defines the function, then `getattr` — with
`globals()[…]` next. Approximating Python's name binding with `ast` is a thing this repository
has paid for before. What it was protecting is covered exactly and at run time by the test that
counts the calls made while the dashboard renders, and the invariant it claimed was stronger
than the one that matters: *one implementation* compares a target against a receipt, so two
views cannot come to different answers about the same question. A check that cannot be made
true is worth less than the one that already is. The values are escaped
rather than stripped, because silently removing what a document said is its own way of
misreporting it.

Prose and code spans are escaped differently, because a backslash escape does nothing inside a
code span: CommonMark reads its content literally, so a value escaped for prose still closes
the span with its own backtick and lets what follows become emphasis, a link, or another line
that reads as the receipt's verdict. A value inside a code span gets a delimiter longer than
the longest run of backticks it contains. The producer's source URL is a code span rather than
a Markdown autolink for the same reason — an autolink also reads its content literally, and a
`>` from disk would close the construct the renderer opened. And rig's own prose in the JSON no
longer carries markup: a receipt's `basis` is read by a JSON consumer, by the CLI and by the
page, and only one of those three reads a backtick as markup.

The rule is checked by rendering the page twice — once from harmless values, once with every
field poisoned — and comparing the structure. What is under test is the page's own shape rather
than the fields somebody remembered to escape.

**And a comparison is made in one place.** `assurance_target.evaluate` has exactly one caller,
and `assurance-target`, the receipt, the Markdown page and Mission Control all reach it through
`assurance_wiring.projection`. The command used to evaluate its own target directly beside the
receipt's, which is two places the same comparison could come to different answers. The
call-site check parses the source rather than searching it, because a text search matches every
comment that names the function — which is how it passed while a second caller existed.

**And failing to look is never reported as having looked.** An unreadable runs directory used
to become zero tasks, which printed "no task has recorded an assurance target yet" — a verdict
the page did not establish and cannot. A missing directory is still a cold start; anything else
says the records could not be read, and a page where *every* task failed to open no longer
prints "no task has recorded an assurance target yet" — a statement about the targets, reached
without looking at one.

**And the target's own schema is closed.** `validate` accepted every extra root field, so
`{"schema": ..., "axes": {...}, "waive": true}` reached the floor, the receipt and the
dashboard with the field it asserted discarded — the author believing the target said something
no part of rig ever read. The characters escaped in prose are chosen by a rule rather than
assembled from the attacks somebody thought of: every character that can *begin* an inline
construct. `_` was missing, which is how a reviewer got `_forged verdict_` onto the page while
the poison fixture, containing `*em*` and no underscore, agreed nothing was wrong.

Two things the escaping does not do, said here rather than left to be discovered. GFM turns a
bare `www.example.com` into a link with no special character involved, and no escaping prevents
that without editing the text — a value that looks like a URL may become one. And *one
implementation* comparing a target against a receipt is not the same as one comparison
happening: `assurance-target` answers about the file named on the command line while the
receipt has already answered about the file in the run. Both are real questions with
legitimately different answers, so the command prints both — showing one while the other
existed would let a reader take the answer to the question they did not ask (#479).


The intent contract now reaches the three places that can use it. `workbench.py intent-derive
<contract> --against <file> --floor|--target` derives a workflow floor or an assurance target
from a contract's declared requirements, and the assurance receipt carries the goal back beside
what the gate ruled on.

#435 gave the goal a shape that can refuse. This is what other parts of rig may read out of it,
and — more of the work — what they may not.

**A conclusion cannot create a requirement.** `synthesise` refuses a floor built from the
proposal it is checking; the same rule reaches back one step. A requirement rig *inferred*, or
one a planner *proposed*, may sit in the contract — recording it is the point — but it does not
put a step on anybody's floor. Only what somebody declared does, and it arrives carrying which
somebody: a user's request becomes `operator-requested` and a policy's requirement stays
`policy-required`, because a person can withdraw what they asked for and a policy requirement is
not theirs to withdraw. Two declarations wanting one step and disagreeing about who requires it
is refused rather than resolved by which was written first.

**A contract does not get to name what it cannot see.** Its requirements say what would show
them true: a test id, a gate criterion, a step. Those names are matched against what exists, and
a name matching nothing grants nothing rather than inventing it — with one report for the case
that was *meant* to be a step and is misspelled, since nothing here can tell that from a test id
and silently dropping both would be worse than naming the candidates.

**And it does not fill in an axis no requirement could speak to.** An assurance target has axes
for isolation, verification, provenance, approval and the gate; only a gate criterion is a
statement about assurance. So a contract resting on gate criteria asks for a gate that passed
and says nothing about the rest — reading "production quality" out of a goal and filling in four
axes is what `assurance_target.VAGUE` refuses, and generating it here would route around that
refusal by writing the words for the author. A contract that asks for nothing produces no
document rather than an empty one, because a target requiring nothing is met by everything.

**And the contract's own schema is closed.** `intent.validate` accepted keys `load` then
dropped, so a requirement carrying `mandatory: true` or `axis: "isolation"` passed — leaving the
author believing the contract said something it no longer says, and a receipt claiming to copy
the contract copying most of it. A contract could look like it spoke to an assurance axis while
nothing read that. The accepted keys are derived from the records that hold them, so a field
added to `Requirement` is accepted without anyone remembering to.

**And it is read the same way wherever it is read.** Three entry points read a contract from
disk — `intent`, `intent-derive` and the receipt — and each had its own parser until a review
pointed out that a duplicated key was refused by two of them and reported as a valid
declaration by the third. `intent.read` is the one reader now: a rule each caller has to
remember is a rule one of them will not. It refuses a duplicate key, JSON allows a key twice and `json.loads` keeps the
last one silently, so a duplicated `origin` would turn an inferred requirement into a declared
one and the receipt would present that parser choice as what the contract recorded. A contract
that is there and unreadable is reported as that, not as no contract — the file's digest is in
`sources` either way, and the two are different situations with different next steps.

**And the page says it too.** The Markdown receipt gained an Intent section — the goal, the
assumptions, the non-goals, each requirement with its origin, where it was said, what would show
it and what the gate actually ruled on, and the open questions. Evidence and gate observations
are kept apart there: a requirement resting on a test nobody wired to this gate and one resting
on nothing are different requirements, and a page that printed only the gate's view made them
the same one. The renderer's claim is that it is the same model as the JSON read aloud, so a
section the JSON gained and the page did not would be that claim going false.

**The projection copies.** *A derived view re-judges nothing; it copies decisions from the
records that made them.* The receipt reads the goal and the requirements out of the contract,
reads each named criterion's status out of the gate block, and stops. Whether the criterion
passing *satisfies* the requirement is `intent.status`'s question and a human's after that. A
requirement nothing checked is reported with an empty list rather than omitted — "nothing checks
this" is the fact `intent.unverifiable` exists to surface, and dropping those rows would make a
contract look better on that page than it is (#476).

**And nobody has to remember any of it.** Six review rounds found the same defect in six
different places: a contract field accepted by a schema and dropped by a loader, a parser that
was not the one parser, a page that never learned about a block, a projection that copied by
hand. Five of those were fixed by deriving that layer from the dataclass, and the sixth round
found a layer that had not been. Deriving one more place only moves where the next person
forgets. So the rule is declared once — `intent._CODEC` says how each contract field is read,
and the receipt declares which fields its page reads aloud — and both declarations are checked
against the contract *at import*, not in a test: a field nobody said how to read cannot be read,
and a field nobody said whether to print cannot be left off without saying so. A test can be
skipped, deselected, or simply not run by the person adding the field; an import cannot.

Withholding a field from the page is itself a decision that has to be visible. The declaration
began as a set of names, which was the guard's own way out — adding a name satisfied every check
and the page said nothing, so a field could still be left off quietly by the mechanism written
to stop that. It is a field-to-reason mapping now: a reason is required, and the page names each
withheld field and why. Nothing is withheld today.

**And a criterion recorded twice is not a verdict.** The receipt indexed the gate's criteria by
name, so a criterion the gate ruled on twice gave the page whichever record came last — a
verdict that depended on the order two records sat in. Any repeat, not only a disagreeing one:
a gate that ruled once on one criterion is what this page copies from, and anything else is a
record it says it cannot read a single verdict out of. Marked where the gate block is built, so
the section that lists every record and the section that looks one up by name cannot come to
different conclusions about the same file. An open ambiguity now reads *would be settled by*
rather than *settled by*, which is what `resolved_by` has always meant (#476).

A trustworthy change should explain not only what changed, but why it exists, what evidence
supports it, and what happened after it shipped. `workbench.py provenance <graph> <node>` traces
a node's chain in both directions, with confirmed and inferred relations kept apart.

Git records what changed and a receipt records what a task achieved. Neither records why the
change was wanted or which requirement it was answering, and a reader reconstructing that from
six files reconstructs it differently each time.

**It does not infer.** Deciding that this commit implements that requirement is reading two
things and concluding a third — an agent's work, and a module that called a model to do it would
leave nothing a gate could check and nothing a mutation could falsify. Edges arrive already
drawn.

**The partition says whether you can rely on getting here; a flag says why not.** A step that
was somebody's conclusion, and one whose authority nobody could find, both make everything past
them unreliable — but "an agent concluded this" and "this names a receipt that is not there" are
different problems with different fixes, so the answer carries both dimensions rather than one
that means either — and the reason travels: an edge somebody checked, sitting past one whose
receipt is missing, is unreliable for *that* reason, and saying the wrong one sends a reader
looking for an inference that is not there.

**A guess and an observation are not the same edge.** The tempting rendering of "an agent
thought so" is an edge that looks exactly like one somebody checked, and a reader following a
chain then has no way to see where it stopped being evidence. Every edge says how it was
established and who established it; an edge that cannot say either is refused; and a trace
returns the two kinds apart rather than merged, in both directions, with the basis on every line
rather than in a heading somebody scrolls past.

**And `confirmed` names a kind of thing somebody could go and look at.** An authority is
written as `receipt:…`, `git:…`, `person:…`, `policy:…` or `agent:…`, and an `agent:` authority
cannot be `confirmed` however sure it sounded — a conclusion is not an observation with a
different adjective. **And a reference nobody could find is not
confirmation.** `trace` takes a resolver and reports whether every confirmed authority
it reached was actually looked up. An authority whose reference is missing makes its edge — and
everything past it — unreliable, because naming a receipt is not the same as there being one.
The resolver answers `found`, `missing`, or `not-checked` for a kind it cannot check from here:
a `person:` cannot be looked up on this machine, treating that as "checked and absent" would
punish the honest answer, and calling it resolution is how a graph full of them comes back
saying it was verified. A `receipt:` is a run name and not a path, the resolved
candidate has to land inside the store, and the record there has to be a real file, shaped like a run
record, saying it belongs to that run — "a file exists here" is not "this receipt exists", and
neither is an object repeating the directory name. A `git:` authority is an object id and not a
revision: `git:HEAD` resolves now and names something else later, which is the opposite of what
an authority in a provenance record is for — otherwise any accessible `task.json`, reached by an
absolute path, a `..`, or a symlink, would confirm an edge that named it. Resolution reaches the
invalidation section too — a missing receipt presented as the stale relationship's
authority, or as the authority claiming it went stale, is the same unchecked assertion wearing
the same `[confirmed]`, in the section where a reader is deciding what to stop trusting.

**And no name may draw a line.** The report writes one line per edge with its basis on it, so
an authority containing a newline writes a second line that says whatever it likes — and a
reader scanning for `[confirmed]` finds one. Control characters, format characters and look-alike
spaces are refused in every field the report prints, by Unicode category rather than by a list,
which is a superset of what this repository already calls invisible.

**It gives a second copy of the verdict nowhere to live.** An edge to evidence names the
receipt and stops. Copying "passed" into the graph would make two places that answer "did this
verify", and the one that drifts is always the copy — `assurance.py` is the authority (#428). So
the schema defines no field for a verdict and refuses a document that adds one. What it cannot
do is police free text: a node labelled `"passed"` validates, because a label is prose for a
human and nothing here reads it. The guarantee is that no *field* carries a verdict, stated at
that width rather than one wider.

**Invalidation is a fact the graph carries, not a deletion.** Evidence goes stale: the target
moved, a later run said otherwise. Removing the edge would leave a chain that reads as though
nothing had ever supported the change, which is a different and worse claim than "this was
supported and then stopped being". So an invalidated edge is reported alongside the others and
not subtracted from them — it was confirmed, and it was invalidated, and a caller deciding what
to trust needs both. **And an invalidation has a basis of its own**: somebody may have concluded
the evidence went stale rather than observed it, so what said so is carried with it. Dropping
that would make a guess about staleness read exactly like a confirmed one, which is the rule
this module is built on, applied to the one place it applies to itself.

**A chain is followed to the end, and is as good as its weakest link.** A commit that
implements a requirement that satisfies a goal answers "why does this exist" with the goal;
stopping at the requirement answers it with a restatement. The traversal is cycle-safe, and the
list an edge lands in is about **reaching it from the node asked about** rather than about the
edge alone: once a step was somebody's conclusion, everything further along is reachable only if
that conclusion holds, however carefully the later steps were checked. Each entry still carries
the edge's own basis, and the report says when a checked link sits past an inferred one —
"somebody verified this link" is worth reading, and it is not the same as "you can get here".

**And the answer does not depend on the order the edges were written.** A node reachable by a
checked route and a concluded one is walked as both, so which arrives first is a fact about the
file rather than about the answer, and neither route is concealed behind the other. The
invalidation section carries the path basis too — it is exactly where a reader is deciding what
to stop trusting (#436).

Minimise the cost of producing the required assurance, never the requirement itself.
`workbench.py budget-plan <plans> --budget <file>` chooses among candidate plans that clear the
assurance floor, and says so rather than picking the closest one when none does.

Applying the heaviest verifier, drill and approval to every change buys quality with money and
waiting; dropping a mandatory gate to save either buys the money back with something that was
not ours to spend. What is left is a constrained problem rather than a trade-off.

**It does not estimate, and it does not plan.** What a verifier will cost, how long a runtime
will take, which plans are worth considering — an agent's work, and a module that called a model
to do it would leave nothing a gate could check and nothing a mutation could falsify.

**The floor is not the plan's to state, and it has to be stated.** `Budget.required` is built
by the caller from the policy and the assurance target (#434), and has no default, because "the
caller did not supply the floor" and "the policy requires nothing" are the same value with one
and opposite answers without one. `Budget.task` likewise: a budget carries the floor, so one
prepared for a wording change applied to an authentication change is a weaker floor arriving by
mispairing, for the reason `synthesise` builds its floor that way
and `route-team` builds its constraints that way. A plan that does not produce what is required
is not a cheaper way of doing this; it is a different, smaller thing, and it is excluded before
anything is ranked.

**And a quantity nobody can hold is not a quantity.** `Infinity` is what Python's JSON decoder
makes of the token by default: as a limit it disables the constraint, as a price it clears every
limit, and as an exchange rate it makes every latency worth nothing, silently collapsing
`balanced` into `cheapest`. `NaN` loses every comparison it is in, including against itself.
Both are refused at the decoder and again by the field rule.

**An unknown cost is not a low one.** A runtime that cannot report what it charges says
`unknown`, and `unknown` does not compare — it is not zero, not cheap, and it does not win
against a plan that measured itself honestly. The same rule one field over: a plan that cannot
say how long it takes is not the quickest, and it fails a latency budget rather than passing one
it cannot be compared against.

**Balancing money against time needs the caller's exchange rate.** `balanced` requires
`seconds_per_unit_cost`, because adding dollars to seconds is a category error and what an hour
is worth is a judgement about the caller's situation rather than something this module can
supply.

**Running out of budget is an answer, not a discount.** When nothing affordable clears the
floor, the result is `exhausted` and names what someone can do about it — block the change,
raise the budget, use a different runtime, or relax the target. That names the moves allowed to
follow, not a record of anyone making one — recording who relaxed a target is somebody else's
job. A closed vocabulary, because "we lowered the target a bit" written as prose in a field
nobody parses is exactly the silent downgrade the design principle rules out.

**Refusing is not running out.** A record that cannot be read, or a budget that belongs to
another change, is refused with no moves offered: refusal does not establish that nothing
affordable clears the floor, and offering "relax the target" to someone whose file was
malformed would be a fail-open with a helpful tone (#439).

Rig should learn which team works best for this kind of change, without optimisation weakening
the trust boundary. `workbench.py route-team <routing> --constraints <file>` checks a role
assignment against the constraints a policy states and exits non-zero if optimisation reached
past one of them.

Fixing one provider for every role wastes money on wording changes and underspends on
authentication boundaries, and this repository already holds what it would take to choose
better. But a router that may also decide which constraints apply has been handed the question
it was supposed to be constrained by.

**It does not choose.** Deciding a provider is right for an authentication review is reading
evidence, weighing it and concluding — an agent's work, and a module that called a model to do
it would leave nothing a gate could check and nothing a mutation could falsify. An assignment
arrives as a record of what was chosen and why.

**Hard constraints outrank optimisation, and the constraint set is not the record's to state.**
Approval, capability, independence and required roles are built by the caller from the policy,
for the reason `synthesise` builds its floor that way: a constraint the thing being checked
gets to state is not a constraint. Independence is additive — a policy may name more roles that
must be independent and has no field that could name fewer, because a setting that could shrink
it would let a policy file turn the developer into the judge. An allowlist that is absent states
nothing; one that is present and empty names nobody, and the two mean opposite things. And the
constraints document itself is validated before anything is turned into a set: `frozenset(...)`
takes whatever iterates, so `{"approved": {"evil/model": false}}` would otherwise approve the
provider it looks like it denies. The document is **required** rather than defaulted, because a
caller who forgot it — or whose policy failed to resolve — would otherwise get an admission
meaning "nothing was enforced". **An assignment cannot be built in a state the document would be refused in**, and
`Constraints` validates its own contents, so building either directly is not the way around the
document's schema — four review rounds found the same defect in four places, each time because
the JSON path was checked and the object was not, so the rule now lives in one function both
reach: shape is checked where JSON can still be
seen (a dict becomes its keys, a string its characters) and contents where any caller reaches
them.

**Two names for one backend are one backend.** Comparing the strings a router wrote would let
`vendor/model-x` review what `vendor-alias/model-x` implemented, which is one model grading its
own work under a second name. So the policy states the canonical identity of every provider,
independence is compared on that, and a name the policy cannot resolve refuses the check rather
than passing it. Stating `identity: {}` is how a policy says its names are already canonical —
it has to be said, because silence would mean the check ran on whatever the router called
things. A backend has to be terminal: with `dev-alias → model`, `judge-alias → backend` and
`model → backend`, one hop makes two names for one backend look independent while the policy
itself says otherwise, so the mapping is refused rather than resolved at compare time. And a
validated `Constraints` copies and freezes what it was given, because `frozen=True` stops the
fields being replaced and not the sets behind them from being emptied afterwards. Cost and a provider's measured excellence are arguments
about *which* approved, capable, independent provider to pick, never an argument for picking
one that is not.

**An unmeasured provider is not a good one, and the router does not get to say it is measured.**
The tempting rendering of "we have no data" is a blank, a zero or a default, and all three read
as "fine" next to a measured competitor. So a selection states how well it is known —
`measured`, `shadow`, `unmeasured` — `evidence_count` is required rather than optional because
the interesting value is zero, and both `measured` and `shadow` on zero observations are refused
because whatever either means, both mean something was observed.

But that statement is *reported*, not believed. Which providers count as measured for an
assurance role is `Constraints.measured`, stated by the policy alongside every other constraint,
because a record that could assert the fact unlocking its own eligibility is stating its own
constraint — the pattern this module rejects everywhere else. A role nobody is listed as
measured for admits nobody, for the reason an empty allowlist names nobody. Shadow evaluation is
what you do to a provider before trusting it with a verdict; promoting it is somebody's decision,
recorded in the policy, rather than a word in a routing record.

**Two providers for one role is two answers to who is accountable for it**, and it is refused
before anything else is computed. Every later check reads a role-to-assignment map, and such a
map cannot represent two — so a verdict drawn from it would be about whichever of the two the
map happened to keep, offered next to the structural problem as though it were as reliable.

**A verifier that is the developer is not a verifier.** The same provider on both sides of a
role whose whole value is being other than what it judges produces a verdict about its own
work, and no evidence about how good that provider is makes that verdict independent.

An admissible routing does not say the team is a good one — which approved, capable,
independent provider performs best is a judgement about evidence this module does not have. It
says optimisation did not reach past the constraints to get its answer. What the record called
unmeasured is reported either way, named as the router's word rather than a measurement that
happened, and carrying the role each provider was put in.

**And the constraints have to be the ones chosen for this change.** A router free to label an
authentication change as a wording change would be picking which constraints apply to it, so
`Constraints.task` is stated by whoever chose them and compared against what the record says it
routed. Constraints that name no task check everything else and do not pretend the binding held
(#438).

An autonomous developer may decide how to pursue a goal. Not whether its own result is
trustworthy. `workbench.py dev-loop <task> <cycles>` judges a loop's record against its bounds
and its handoff against the receipt, and exits non-zero unless the record is about that task,
the loop declared itself done, it is not observably stuck, and what the receipt points at is a
fixed object that the record's last cycle also names.

A loop that researches, plans, implements, tests, reviews and repairs is a good way to reach a
result and a terrible way to judge one: every signal it would judge itself by is a signal it
produced.

**It does not run the loop.** Choosing what to research, how to repair, what to plan next is
reading, judging and concluding — an agent's work, and a module that called a model to do it
would leave nothing a gate could check and nothing a mutation could falsify. Cycles arrive as a
record of what happened.

**It stops the loop on evidence, not on the loop's account of itself.** An unbounded repair
loop is a way to spend a budget without reaching a result, and "I am making progress" is
exactly the judgement a stuck loop gets wrong. So the three bounds are computed from the
record — how many cycles ran, whether the same failure signature keeps coming back
consecutively, and whether the work product changed at all — and a caller who said nothing
about bounds still gets them, because making the caller opt in to being bounded gets the
failure mode wrong by omission.

**A developer's PASS is not an assurance PASS.** The loop's own `tests passed` is recorded as
what it is, under `self_reported`, and the schema has no field for a verdict: `gate`,
`accepted`, `verdict` and their spellings are refused rather than stored. What accepts a change
is `build_acceptance` and the receipt, and neither of them reads this document. The command
prints the verdict before the account, because a reader who sees the account first reads the
verdict as agreeing with it.

**A bound that has been passed stays passed.** The runs are counted anywhere in the record and
not at the end of it. A loop that ran past a bound and then produced one different cycle did not
un-run past it, and reading only the trailing run would let it clear the evidence by continuing
— which is the one move a bound exists to prevent.

**Two of those three bounds are only as good as what the loop can author, and the answer says
which.** `product` must be spelled like a git object id, which stops a counter standing in for
work — but 40 hex characters is a spelling, and existence is barely better, because a stuck
loop can name a different object that was already in the repository every cycle. Ancestry of the delivered
commit is not enough either — that history has no lower bound, so the loop can reach back past
the task. So `must_stop` takes a `History` and asks it two things: is this commit **inside this
task's range** (a descendant of the base the receipt records, an ancestor of the head it points
at), and does each cycle **build on the one before it**. A borrowed object fails the first; real
in-range commits reported in no particular order fail the second.

That is a chain inside a range, and it is worth saying exactly what it is evidence of: the
record describes commits leading from where the task started to what it delivered, in order. It
does not establish that the loop occupied those states — a loop could build the chain after the
fact — and nothing here says it did. Without a `History` the bound falls back to the loop's own
account, and the result carries `products_related` so a reader is told which answer they got.

`failure` cannot be constrained at all: canonicalising a test failure means reading structured
output this module never sees, so a loop appending a nonce to the same failure defeats that
bound. It is a backstop against a loop that is honestly stuck, not a control against one that
is not, and the docstring says so rather than letting a reader assume otherwise.

**The record says which task it is about, and it does not get to restate the goal.** Two loops
pursuing different goals that end at the same commit are indistinguishable without the task id,
and reading one as the other's completion would credit a task with work done somewhere else.
The goal is compared against the task's recorded input for a sharper reason: a loop free to say
what it was pursuing decides what "done" was measured against, which is the decision this
boundary exists to reserve.

**A handoff and its record name the same commit, or it is refused.** `immutable` is
read from the receipt (#428) rather than re-derived, so there is one answer to "what was
verified" and not two — and the loop's last product must equal that commit, because a record
paired with some other immutable commit on the same task describes work nobody in it did. That
the loop *made* it is not something either half establishes.
Reaching the cycle limit does not block a handoff; spending the budget is a reason to stop, not
a reason to throw the work away.

**An admissible handoff is a narrow claim, and it is worded as one.** It says the loop declared
itself done, that it is not observably stuck, and that what it points at is fixed and is what
the record's last cycle names. It does not say the loop made it, and it does not say the loop
converged, because "not hitting two stuck-loop
thresholds" is not convergence and a word this module cannot check is one it should not use
(#431).

A workflow may adapt to the risk; what it is trusted for may not.
`workbench.py synthesise <workflow> <catalog> --required <floor>` validates a proposed
workflow against the components that exist and the steps a policy requires, and exits
non-zero if the proposal went below that floor.

A fixed recipe is either too heavy for a wording change or too light for a change to an
authentication boundary, and picking one per task is what a planner is for. A planner that
can also decide which gates apply has been handed the question it was supposed to be
constrained by.

**It does not classify.** Deciding that a diff touches an authentication boundary is reading,
judging and concluding — an agent's work, and a module that called a model to do it would
leave nothing a gate could check and nothing a mutation could falsify. Classification arrives
as a payload, with its reasons.

**The floor is computed from the policy, never read from the proposal.** `build_acceptance`
already states the rule for gate criteria: a project file or an org policy may add to a gate
and never take built-ins away. This is the same rule one level up, about which steps a
workflow contains. A mandatory
step the planner left out is restored and the omission reported — and so is the subtler move,
keeping the step while relabelling it as the planner's own idea: it still runs, and every
later reader is told it was optional.

**Nothing on a mandatory step is the planner's to author.** Three ways to shrink the floor
while appearing to hold it turned up in review, one after another: leave the step out; keep it
under a different source; keep it under the right source and rewrite the reason to something
weaker than what the policy said. The third passes any check written against a list of fields,
and the fourth would arrive with whatever field gets added next — so the comparison is the
whole step against the whole floor entry. Both halves are reported: what the policy requires,
and what the planner proposed instead, because the judgement a reader is making lives in the
difference.

**The floor carries who requires each step, not just which.** `operator-requested` counts as
mandatory too, because a planner deciding a human asked for too much is the same overreach
wearing a friendlier name — but a floor of bare ids cannot enforce that. Once a step is
deleted its source goes with it, and "the planner dropped what the operator asked for" reads
exactly like "nobody asked for it". So the caller builds the floor from the policy and the
operator's instructions, and each entry keeps its source through to the report: a restored
step says whether an organisation requires it or a person asked for it, which decides whether
it can be withdrawn and by whom.

**The floor is the stricter document, not the unchecked one.** A restored step goes straight
into the resolved workflow, so anything the floor may hold is a way to get a step past
validation by putting it on the floor instead of in the proposal. So a floor entry is checked
the way a proposed step is — a registered id, a reason, and a source that can actually require
something: `policy-required` or `operator-requested`, never one of the three the planner
reaches on its own. Two authorities requiring the same step is refused rather than resolved by
argument order, because keying by id would silently keep one of them and decide whether a
person may withdraw the step on how the caller happened to build the tuple — and **both files are read
with a hook that refuses a duplicate key**, because JSON allows one and `json.loads` keeps the
last silently. Two authorities written in the other order gave the other answer before the
check ever saw them; a step whose `reason` appears twice reaches the comparison saying only
the last one. Every check here compares what was authored against what is required, and a
parsed dict has already thrown one of the two away.

**A `floor_held` is about components, not about what a component then checks.** A resolved
step is an id, a source and a reason; it carries no criteria, no configuration, no arguments.
What a gate criterion must hold is `build_acceptance`'s to protect, and it protects it there.
So this command answers "does the workflow contain the mandatory steps, attributed to whoever
requires them" and stops — claiming more would be vouching for a guarantee it has no way to
inspect. For the same reason the schema is closed at both levels: a `skip` on a step
or a `waivable` at the root is refused rather than accepted and dropped, because accepting a
key and discarding it is the module deciding a planner did not mean what it wrote. And the
short form of a floor entry is a reason *string*: `{"review-diff": null}` coerced to `"None"`
would be a floor protecting prose nobody wrote.

**The result is a report about a workflow, not a workflow.** `resolve` returns
`rig.workflow-resolution/v1` with the resolved workflow nested under `workflow` — labelling
the whole thing `rig.resolved-workflow/v1` would make its own `corrections` a key that schema
does not define, and feeding the result back would be refused for a reason the caller could do
nothing about. `report["workflow"]` round-trips. A command failure answers in
`rig.workflow-resolution-error/v1` for the same reason: nothing was resolved, and `status` and
`error` are keys neither of the other two schemas defines.

**Registering a component is an act, not a shape that iterates.** `set(json.loads(...))`
takes whatever it is given: a JSON object becomes its keys, so `{"run-arbitrary-shell": false}`
registered the component it looks like it denies, and a JSON string became its characters. The
catalog is the whole basis for the next paragraph, so it must be an array of component ids or
the command refuses.

**A component nobody registered is refused.** "The planner invented a step" and "the planner
selected a step" look identical in a resolved workflow, and only one of them is what
synthesis is for. So is a step that cannot say why it was selected: a workflow that cannot
explain its own contents is a list, and the resolved workflow is meant to be evidence.

Restoring the floor is not the same as accepting the proposal. The command corrects what it
can and still exits non-zero, because a caller told 0 would believe the planner produced
something runnable as proposed (#432).

What assurance was *asked for* can now be written down and checked against what a task's
receipt recorded. `workbench.py assurance-target <task> <file>` compares a
`rig.assurance-target/v1` document against the receipt and exits non-zero unless every axis
was met — because a gate that exits 0 on an unmet target is not a gate.

The receipt has always recorded what a task achieved: which tree the work was written to, how
independent the checker was, whether the accept record still verifies, which approvals were
taken, how the gate ruled. What it never had was the other half, and without that "the gate
passed" is a fact about rig's defaults rather than about anyone's requirements.

**A target may only name an axis the receipt can answer.** `_isolation` refuses to borrow the
evaluation vocabulary's `none/agent-policy/os-enforced` ranking, because a git worktree keeps
a change off the main tree and stops there — claiming an OS boundary git is not holding would
be the receipt lying about itself. So `isolation: os-enforced`, the example the issue itself
uses, is refused: demanding it would demand an answer nothing here can give.

**An axis rig cannot observe is its own outcome.** `unobservable` is not a softer `unmet`.
`unmet` says rig looked and what it found falls short; `unobservable` says it cannot look, and
carries the receipt's own reason for not having — "governance is inactive for this
repository", not a placeholder. A caller folding them together reads "we do not measure that"
as "we measured it and it was insufficient", and acts on it. And a block that says
`observed: false` is believed over any value left sitting beside it, so "we did not check the
gate" cannot become "the gate passed".

Every value a target may name is reachable from a receipt the *producer* can actually write,
and two tests hold that: one pins each value to a receipt shape that achieves it, and one
reads `assurance.py` itself, because a hand-written receipt proves the comparison works and
proves nothing about what rig can ever record. Two values got in that way and neither was
reachable — `verification: independent`, which `_verifier` never asserts because rig's review
dispatches subagents whose identity never reaches task state, and `approval: none`. `approval: none` in particular was there and was not:
`_approvals` reports the absence of decisions as `observed: false` with a reason, so "no
approvals taken" is something the receipt declines to assert rather than something it
records — and a target asking for it would have validated and then reported `unobservable`
forever, from a table whose whole claim is that it mirrors what the receipt can say.

**"production quality" is not a target.** It names a level without naming what it is, and rig
cannot explain a mapping it did not receive — so it refuses the word by name and says what to
write instead, rather than inventing a mapping and defending it later. Nothing downgrades
quietly: there is no nearest-acceptable and no rounding in rig's favour (#434).

What was asked for now has a shape something can check against. `workbench.py intent <file>`
validates a `rig.intent-contract/v1` document and reports what it leaves open — exit 0 for a
contract, 1 for a document that is not one, 2 for a file it could not read.

Ten runs in this repository carry a hand-written `requirements.md`: the goal in the user's
own words, then the Issue's criteria copied verbatim, each with the thing that satisfies it
and the test that pins it. Ten times the same artifact, and every one of them a claim nobody
could check — that the criterion quoted is what the Issue said, that the test named exists,
that a requirement attributed to the user really came from them. This is that artifact with
a schema, and the schema's job is to make those claims refusable.

**It does not generate one.** Turning a sentence into requirements is reading, judging and
deciding, which is an agent's work; a module that called a model to do it would leave nothing
a gate could check and nothing a mutation could falsify. Generation happens elsewhere and
arrives here as a payload.

**It does not promote a guess into a request.** Where a requirement came from is recorded on
it, extending the vocabulary `build_acceptance` already uses for gate criteria rather than
starting a second one: `policy-required` is its `origin="policy"`, `repository-derived` the
read-it-from-the-repo case. The two that assert someone else said something — `explicit-user`
and `policy-required` — are refused unless they say *where*, because an assertion about a
third party that names no source cannot be checked by anyone, including the person it is
attributed to. `inferred` and `proposed` are rig's own reading and are never counted as
declared, which is the distinction `caller.Caller` already draws for callers.

**It does not let an unmeasured thing pass as a measured one, and it does not condemn one
either.** `status()` takes what was *recorded* about each piece of evidence — `passed`,
`failed`, or `unobserved` — and copies those decisions rather than deriving them. It took a
review round to get that right: the first version took a set of ids that had passed, which
made absence mean both "ran and failed" and "never ran", so a contract whose tests had simply
not run yet reported its requirements as unsatisfied. A requirement is `unsatisfied` only
when something checking it is recorded as having failed; evidence merely unobserved leaves it
`unverifiable`, which is a different answer — a caller that collapses them reads "nobody
looked" as "it failed", or the other way round. A state outside that vocabulary is refused
outright rather than read as the nearest one: `unobserved` is itself a claim — that someone
looked at the ledger and found no entry — and a record saying something unrecognisable has
made no claim at all.

The command reports structure and not a moment. It runs nothing, so it counts how many
requirements have anything checking them and how many are still rig's own reading; asking for
the verdict instead would call every evidenced requirement unverifiable and describe it as
having nothing to check it. A
contract with no requirements at all is `unverifiable` too, so a run cannot claim its intent
was met by never writing one down. An ambiguity is kept as an ambiguity and must say what
would settle it; one that says nothing is a note, and notes close no questions (#435).

rig's state is per repository, not per working tree. `workbench.py` asked git
`--show-toplevel` for where to keep it, which answers *which working tree am I standing
in* — so inside a task's own worktree it looked for `<worktree>/.rig/runs/`, where that
task had never been written. `status` reported the task missing, `board` showed none, and
because the gate, `accept` and every sensor read the same state, the whole flow had to be
driven from the main checkout while the work happened somewhere else.

That split is why a session could not simply be opened where the work is, and opening one
there is the point: an agent session is filed under the directory it started in, so every
task started at the repository root lands in one undifferentiated pile, and `--continue`
inside a worktree finds nothing because nothing was ever recorded there. `new` now ends by
naming the directory and the command that puts the session in it.

The root is asked of git rather than derived: `git worktree list --porcelain` names the
main worktree on its first line and gives the same answer from every working tree of the
repository. Taking `--git-common-dir`'s parent would have assumed `.git` sits beside the
checkout root, which `GIT_DIR` and `core.worktree` can both make false.

The line is between what a repository shares and what a branch owns, and it runs through
more than the run directory. Shared and therefore on the state root: run state, the locks,
the audit log, `.rig/gates.json`, `.rig/packs` — all gitignored local install state, none
of it varying by branch. Owned by the branch and therefore read from the tree the caller is
in: `.claude/rig/recipes`, `.claude/rig/personas`, `.claude/rig/knowledge`, which are
tracked files, so a worktree carrying its own recipe overrides is the tree that has to be
read when a task is routed.

Nothing else moves with it. `repo_root()` had been answering two questions with one call —
*where does rig keep this repository's state* and *what working tree is the caller looking
at* — and only the first one is shared. `invocation_root()` now answers the second, so the
base branch a task is measured against, the head an `import` verifies, and the SHA
`record-commit` records without `--sha` all still come from the tree the operator is
standing in. Read from the main checkout they would have been a different branch and a
different commit, both perfectly valid and both about work nobody was looking at (#471).

rig can tell whether it is running inside an Orca session, and refuses to tell you anything
more than that. `workbench/orca.py` reads `ORCA_WORKTREE_ID` and `ORCA_WORKSPACE_ID` —
`<uuid>::<absolute path>` — and starts no process doing it.

The restraint is the feature. On one host, in one shell, at one moment, all three of these
were true at once: the session variables were set (confirmed by the process tree, where the
shell running rig was a child of Orca's relay), `orca` was on `PATH`, and every `orca`
subcommand failed immediately after its handshake because the far side was not servicing
requests. A detector that answered "available" from either of the first two facts would
hand `--runtime orca` a backend that dies in `create()` — the mirror image of the failure
`runtime.select` exists to prevent, refusing to downgrade silently only to upgrade into
something that cannot do the job.

So the two axes are reported apart, and the second is reported as unmeasured rather than
guessed: `cli` carries `observed: false` and a reason, never a verdict, because nobody
looked. Asking `orca` whether it answers is a question with a cost and a failure mode of
its own, and it belongs to the issue that will act on the answer.

A value it cannot read does not become a value it invents — and that applies to the
identifier, not only to the path. A relative path, an empty path, a path with no id in front
of it, and a value with no separator at all all yield no id and no path, while still
reporting that a session is present: rig is inside something that exported the variable, and
"I could not read what it said" is a different fact from "there is no session", and
different again from handing back the unparsed blob as though it were an identity. rig has
one host's worth of evidence about this format and has never seen a bare id, so treating a
string that failed to parse as one would be an invention — and the reference shape exists to
be recorded. An id carrying zero-width, bidi or newline characters is refused by the same
rule `--caller` is held to, reused rather than restated.

Nothing is registered as a runtime backend — a backend that cannot create a worktree has no
business being selectable (#472).

A refused attested source now says which condition it failed. Four unrelated checks — a
regular file, owned by the pack owner, one hard link, not writable by group or others —
shared one sentence, `is not trusted`, and the commonest of them is a permission rather
than tampering. `git` creates files as `0666 & ~umask`, so a `git clone` or
`git worktree add` performed under umask 002 gives every file mode 664, and every attested
read in that tree is refused while the same commit in a tree created under umask 022 passes.
That shape is hard to see from the message: thirty-one tests failing locally and a green CI,
in trees whose contents are byte-identical. Finding it took a bisect across three working
trees and finally a `stat`. The refusal now reads

    Japanese material source '…' is not trusted: its mode 0664 lets the group or others
    write to it. Run `chmod go-w` on it — and note that a working tree checked out under
    umask 002 gets mode 664 on every file, so `umask 022` before `git clone` or
    `git worktree add` is what keeps this from returning

and reports every condition that failed rather than the first, because an operator who
fixes the permission only to be refused again for the link count learns nothing from the
second refusal the first could not have told them.

`rig-wb hostcheck` gained a `umask` check for the same reason from the other end: it reports
the umask this host carries and the file mode it will produce, so the condition is visible
before a run fails rather than after. It measures the umask and says so — an OK there is a
statement about the next working tree, not about the one you are standing in, which keeps
its 664 until something changes them (#467).

Worktree creation and removal moved behind one seam (`rig_workbench/workbench/runtime.py`).
Rig makes a git worktree because isolation is a precondition for its gate, not because a
git worktree is the only thing that could hold a task — and until now the three places that
created or removed one each spelled out `git worktree add` themselves, so a tool that
manages workspaces of its own could not hold a task without touching all three. `create`
now returns a handle carrying the runtime that made it and a `ref` for identifiers only
that runtime understands, and `remove` takes the handle back, so the backend that made a
worktree is the backend that disposes of it. The git commands the default path runs are
unchanged to the character: `native` is the only runtime this version implements, it is what
an absent one resolves to, and it runs exactly what the inline code ran, `--force` included.
A task now records a `worktree` block beside the `worktree_path` it has always had — beside,
not instead of, because thirty-odd readers still take the path from the older field, and
where the two disagree that older field is the one disposal follows.

Two lines the seam does not cross. **A runtime is not a provider** — which model writes the
code and where the code is written are unrelated questions, and folding them together would
make "run this on Codex" and "run this in another workspace" the same choice, after which
neither could be made without the other; the suite checks that structurally, by parsing
both modules and comparing the names their *code* reaches for, so that naming a runtime in
a comment does not read as depending on one. **The default path gains no dependency** —
selecting `native` asks no other tool whether it is installed, and a repository without one
behaves as it did before. A task recorded before this change reads as `native`, which is a
statement about history rather than a guess: that absence means "written before runtimes
existed", unlike the absences the assurance receipt refuses to fill in, where nobody
measured. No flag selects a runtime yet — the seam is the whole change, and the flag arrives
with the first backend that gives it a second value to take (#461).

`docs/landscape.md` writes down what rig deliberately does not compete on. Forty-four
capability axes across execution, assurance, governance, integration and operations, and
five architectural non-goals — agent fleet scheduling, IDE workspace management,
general-purpose swarm platforms, multi-model consensus, and workflow DSL expressiveness.
The point is not a feature count: it is a test each roadmap item has to pass before it is
taken on, because "add the thing the other tool has" is how a quality layer grows into
another orchestrator without anyone deciding to.

Only rig's column is filled, and every row in it names the file or command that backs the
claim. Every other column reads `unknown`, which is the honest value: nobody here has run
those tools under these axes, and a landscape filled in from marketing pages would be
guesses wearing a table's authority — the failure this repository's assurance work exists
to prevent, pointed outward. Filling a cell has a procedure (evidence, a date, `partial`
over a bare `native`, and popularity kept out of it), and the maintainers of a compared
product outrank anything inferred here about their own column (#430).

`queue cancel <id>` throws away an item that should never run. Until now the only way to
get a mistyped or no-longer-wanted item out of the listing was `queue done`, which is the
record of work that *ran and finished* — so a discard was filed under the completion count
that `cockpit` reads as throughput, and `queue list` could not tell "finished and gone"
from "cancelled and gone" without opening `queue.json`. `cancelled` is its own status:
hidden from the listing like `done`, counted apart from it on the dashboard
(`Nothing pending (3 done, 1 cancelled)`), and terminal for a dependency edge — a cancelled
item never becomes `done`, so a dependent of one is `blocked` rather than left looking
temporarily slow forever.

The decision and the write are one locked compare-and-set. Split across an unlocked read
and a separate write, `queue go` claims the item in the window between them: the check sees
`queued`, the claim wins, the cancellation is written anyway, and the provider's own final
write erases it — so the cancellation is silently ineffective while the operator believes
it took. That is the failure the `running` refusal exists to prevent, and outside the lock
the refusal cannot see it coming.

Refusals rather than surprises. A `running` item cannot be cancelled, because its provider
owns it and will overwrite the status. A `done` item cannot either: rewriting work that ran
and finished as work that never ran is a lie about the past. A `failed` one can, and both its note and the printed line
say it ran — telling an operator that a failed item never ran distorts the audit the status
exists to keep honest. Neither wording claims the item cannot come back, because it can. `cancel` is local-backend only — issue labels have no state for work that
never ran, and the write would post a comment without relabelling or closing, leaving an
item the operator was told was cancelled still listed as queued. A cancelled item stays
retryable from both `queue retry` and Mission Control; allowing only one of them left the
CLI and the page disagreeing about the same item (#459).

The queue can now hold one task back until another task's *result* cleared rig's
acceptance boundary. `queue add "API implementation" --depends-on 1` records the edge in
`.rig/queue.json`, and the condition it waits on is deliberately the stronger one: a
dependency that reached `done` merely had its gate settle — `queue go`'s verifier does not
accept, and accepting is a person's action — so the dependent keeps waiting until the
workbench task it produced reads `accepted`. That link is new too; GO computed the task id
and threw it away, leaving the queue unable to say anything about whether an item's result
had been applied.

One consequence is worth stating plainly rather than discovering: **a dependent cannot
become ready inside a single `queue go`**. Acceptance needs a human and GO does not wait for
one, so GO runs what is ready, records the rest as `waiting` with the reason, prints them as
loudly as the accept reminder, and exits on the same code it always did — held items are not
this batch's items, and reporting them as failures would make every dependency-using queue
look broken to CI.

`waiting` and `blocked` are persisted statuses rather than a filter applied at GO, for two
reasons: it is what survives a restart, and `mission_worker` loops while anything is
`queued` — a dependent parked there would spin the detached worker several times a second
with nothing to run. A dependency that was discarded, failed, points at an id that is not
there, or sits in a cycle `blocked`s its dependent with the reason and the way out; every
GO re-resolves, so a block clears itself once the dependency is retried and accepted. An
`accepted` that overrode a failed gate satisfies the edge — the policy is named `accepted`
and a forced accept is one — but `forced` and the gate status travel with it rather than
being flattened away.

Absence is read in two opposite directions, and both are deliberate. No `depends_on` means
nothing was declared, so the item is ready and its behaviour is byte-identical to before.
Anything rig tried to read and could not — a malformed edge list, an unknown policy, a
missing run record, an item that registered no workbench task — holds the dependent and says
why. The surrounding module returns benign defaults on failure in several places and is
right to, because the batch has already run by then; here the same reflex would start work
whose dependency was never accepted.

Two guards keep a stale link from releasing a dependent. `queue retry` drops the item's
`task_id`, because retrying declares that this item will produce a *different* result; and
an edge is read only once the dependency's own run is `done`, because the recorded id
answers "what did this item produce" while only its status answers "and is that still what
it is producing". Either alone would close the hole, and neither is load-bearing.

Relatedly, `queue go` now claims an item with a compare-and-set instead of marking it
`running` unconditionally. Two concurrent GO processes could always both execute the same
queued item — that predates this change — but an edge raises the cost, because two runs
produce two workbench tasks and only one gets linked, so a dependent can be released
against a result nobody kept. An item another process claimed is reported as `SKIP` and is
not counted in the tally; what happens when GO dies mid-batch is unchanged.

Dependencies are local-backend only: issue labels cannot carry an edge list, and dropping
the flag silently would run the dependent immediately, which is the one outcome it exists to
prevent. Mission Control can obtain the graph as `rig.queue-dependencies/v1` — nodes and
edges carrying a state and a reason, no colour, class or coordinate — and shows the edge and
the held reason on each queue item (#427).

Rig can now be the acceptance boundary for a change it did not produce. `workbench.py
import --head <commit> --producer <name>` registers an external orchestrator's change — a
CI job's, another harness's, a colleague's branch — as an ordinary workbench task. The
mechanism is one line: the task branch is created *at* the imported commit rather than at
the base, so `base..branch` is the external change and `diff`, every deterministic sensor,
the gate, governance, `accept`, the signed provenance record and the Assurance Receipt all
operate unchanged, because there is nothing for them to tell apart. "An imported task
cannot skip verification" therefore holds by construction: there is no second accept path
to keep honest, and the test suite reads the accept, gate and governance sources to confirm
none of them so much as mentions the producer.

The producer's own verdict is recorded and never applied. `--producer-claim tests=passed`
lands in the task record and in the receipt carrying `gate_effect: "none"` in the claim
itself rather than in a footnote, and there is no code path from a claim to
`acceptance.json`. `--producer` / `--producer-runtime` / `--producer-run-id` /
`--producer-url` are kept as provenance and reported as declarations: the receipt's
independence verdict reads `declared-separate`, never `independent`, because rig verified
the commit and not the account of who produced it. Every one of those strings is the first
externally supplied text to reach the receipt, so they are held to `--caller`'s own rule —
zero-width, bidi-control and newline characters are refused rather than stripped, from that
module, so a second and quietly diverging definition cannot come into existence.

`accept` treats a missing diff summary as structural, not overridable even with `--force`,
and a headless producer writes none — so `import` derives one from the imported commit
messages and labels it as what it is: "**No reviewer wrote this**: it is the producer's own
account of its work, restated". `--summary <file>` supplies an authored one, and the receipt
records which of the two it got.

`workbench.py contract <task-id> --json` is the machine answer an external caller branches
on — `rig.assurance-contract/v1`, with `acceptable` (exit 0), `not-acceptable` (1),
`execution-error` (2) and `pending` (3). Four statuses rather than the three the issue names,
because folding `pending` into `not-acceptable` makes a poller read "still running" as
"refused", and folding it into `acceptable` merges something no gate has ruled on. The
separate `execution-error` code is the reason this is a command rather than a flag on
`receipt`: `die` exits 1 for a bad task id, corrupt state and an unmet gate alike, so a
caller reading exit 1 cannot tell a refusal from an outage — and both wrong readings are
costly. `contract` never calls `die`. A change a human accepted over a failed gate reports
`not-acceptable` with `final_status: accepted-over-failed-gate`: it was applied, and rig did
not vouch for it. The mapping is checked against the receipt's own vocabulary rather than a
hand-copy, so a new status fails the suite instead of falling through to a friendly default.

The commit rig verified is pinned at import, and two refs are checked against it: the
producer's, when the caller named a branch instead of a SHA, and the task branch rig owns —
the one `accept` actually squash-merges. The second is the dangerous one, because checking
only the first would let the receipt name the commit rig was handed while a different one is
what lands. Either drifting makes `contract` read `not-acceptable` and `receipt --verify` read
stale. No digest can detect this, since a ref moving rewrites no file, so it is checked by
re-resolving both refs and reported under its own `target_moved` key rather than smuggled into
the list of changed paths — and a branch that has been removed reports `applicable: false`
with the reason rather than `moved: false`, because an absence dressed as a measurement is the
thing the receipt exists to refuse. See `docs/byo-orchestrator.md` (#429).

Mission Control shows the shape a run actually took. The steps list says what ran; it
does not say which steps followed one another, which fanned out to several reviewers at
once, where the machine gate sits, or which of those still needs a person. The new
**Resolved workflow** panel is that shape, served as `rig.assurance-graph/v1` and drawn
by the page from that model alone — nodes carry a `kind` and a `lane`, never a colour or
a coordinate, so a second client reads the same graph without adopting this page's
stylesheet.

It is a projection of a projection: structure and step outcomes from the run's own
`steps.json`, and the gate, approvals and final verdict through the Assurance Receipt,
which is itself a projection. Nothing here re-decides anything, which is what keeps "no
second copy of gate/RBAC/approval logic" true by construction.

Whether a step was serial or a fan-out lives in the recipe rather than in run state, so
it is read from the recipe — the graphed repository's own copy — and only while the step
ids still match, reported as `recipe-drifted` with `pattern` left null when they do not.
Where they match, the value is `recipe-as-currently-defined` rather than `recipe`, with a
`structure_caveat` saying why: a run records a recipe name and never a revision, so an
in-place edit that kept the ids would otherwise be shown as though it had always been
that way. The approval node lists decisions and counts them without deciding whether they
satisfy the rule — that is govern's judgment, made at `accept`, so the node reads
`passed` only once the task is accepted. The `providers` block always has separate
`execution` and `verification` slots even though rig records neither for a workbench task
today, because one merged "provider: unknown" would erase the question the trust boundary
rests on. A fan-out member shows a reviewer's verdict only when `review.json` holds one, and reads
it for what it says — `REJECT` renders as a failure, never as the green a bare "a verdict
was recorded" check would have drawn (#426).

`workbench.py receipt` builds a portable Assurance Receipt — `rig.assurance-receipt/v1`,
written to `.rig/runs/<task-id>/assurance.json` alongside a Markdown rendering of the same
model. Everything a reviewer needs was already recorded across six files that each answer a
different question, and none of which answers "why is this acceptable?". The receipt is the
projection that does, and only that: it re-judges nothing, and the gate status it reports is
copied rather than recomputed.

What rig does not record is the point. The producing runtime and model, the verifier's
identity, and whether the verifier was independent of the producer are all absent from task
state today, so each is carried as `{"observed": false, "reason": …}` — never as a blank, a
zero, or a default that reads as fine to someone skimming. The independence verdict is
`unrecorded`, not `independent`. Isolation says `git-worktree` or `main-tree` rather than
borrowing the evaluation sandbox's `os-enforced` rank, because a worktree keeps a change off
the main tree and does not hold an OS boundary. `--verify` recomputes the digest of every
source the receipt projected and reports `invalidated` when one has changed, by content
rather than by mtime. The signature stays where it was: `accept`'s existing provenance HMAC
is referenced and checked, not duplicated under a second key (#428).

Mission Control's task detail carries the receipt too, built on request rather than read off
disk — a receipt written earlier may be stale, and serving one unlabelled would undo the
freshness check it carries. The stored file is reported alongside it with its own verdict.

`workbench.py new` now records which harness invoked it, with `--caller` available to declare
one. A declaration and an inference are stored distinctly, so the receipt can keep them apart.

The legacy stdio adapter `scripts/mcp_server.py` now isolates by default, closing its one
asymmetry with `rig-mcp`: `rig_orchestrate_run` used to run against the main working tree
unless the caller remembered to pass `isolate: true`, and an absent or null argument fell
through to writing it. Only an explicit `isolate: false` opts out now. `mcp-scan` reads
that default out of the adapter's source instead of asserting it, so the overall verdict
moves MEDIUM → LOW on the evidence, and returns to MEDIUM on its own if the unsafe default
comes back (#419).

`rig-mcp` adds a package-native, client-neutral MCP adapter for one initialized Rig
repository. Read tools are the default; isolated run, gated accept, and confirmed
discard are registered only with explicit write enablement. Streamable HTTP is
loopback-only with DNS-rebinding protection and requires explicit acknowledgement
that the endpoint itself is unauthenticated. Write-enabled HTTP additionally binds
the child Rig identity to one configured operator; multi-user sharing is unsupported.
A customer-run Secure MCP Tunnel client
can target that local endpoint, or a separate authenticated HTTPS reverse proxy can
provide a public direct URL; `rig-mcp` itself terminates neither TLS nor OAuth. Stdio
is available for local hosts.
The adapter delegates asynchronously through an isolated Python invocation to the
canonical CLI, bounds goals and in-memory pipe capture, terminates timed-out or
cancelled process groups, serializes mutations per server, and retains
the former ChatGPT module name as a compatibility import. ChatGPT remains one
documented client rather than defining the server contract.

## [2.6.0] - 2026-08-12

**An evaluation can now run on Claude, and every result says what isolation it was
run under.** codex executes cases inside `codex exec --sandbox read-only`, which the
OS enforces; the Claude CLI has no equivalent, and that asymmetry was handled by
refusing Claude outright — for the judge as well as the generator, since both build
their argv the same way. Refusing is not the only honest response to an asymmetry.
Recording it is.

`_eval_agent_argv` now returns the isolation an argv actually buys, ranked
`none` < `agent-policy` < `os-enforced`, and every result records `provider_isolation`
and `judge_isolation` inside the signed payload — rewriting one to claim a stronger
level fails verification. A case declares the floor its evidence must clear with
`provider_policy.min_isolation`, so agent-level evidence cannot quietly satisfy a case
that needs the OS to enforce it. The one approved case declares `os-enforced`: its task
reads `skills/engine/SKILL.md`, so without OS enforcement the subject being measured
can rewrite the material it is marked against.

Claude runs with `--safe-mode --no-session-persistence --strict-mcp-config`, the tool
set narrowed to `Read,Glob,Grep`, writes denied by name, and its working directory a
`0555` temporary directory outside the repository — `--add-dir` names the repository so
reads still resolve. Policy refuses a write first; the unwritable directory is the net
for the case where it does not.

**The prompt quality gate could not pass, and adding the missing secrets would not
have fixed it.** `validate.yml`'s "Trusted prompt quality evidence" step ran
`rig-wb eval affected-run`, which starts the provider as an external binary. The
job installs `pip install -e .` and nothing else, so no provider exists on the
runner and none is authenticated — the step failed before it measured anything, and
PR #402, the first change to carry a root-tier eval case, was merged red.

CI no longer measures. A maintainer runs `rig-wb eval affected-run` locally against
real providers and commits the signed result to
`evals/evidence/<case-id>/current.json`; CI recomputes the binding and checks the
signature, which needs git and one secret (`RIG_EVAL_ATTESTATION_KEY`) instead of
five plus a provider CLI. The four provider secrets became optional pins rather than
preconditions, and the step still fails closed: no key, no pass.

Committed evidence forced the binding to change. `execution_commit == HEAD` cannot
hold for a file in the repository — committing it moves HEAD past the tree it
describes — and requiring the measured commit to be HEAD's *ancestor* instead only
holds for one of the three merge buttons this repository has enabled. Squash and
rebase both rewrite the branch, so the measured commit is gone or is nobody's
ancestor: the PR check is green and the push to the default branch immediately
after the merge is red, recoverable only by measuring on the default branch and
pushing straight to it.

So the binding is the measured **content**. Evidence signs the object id of every
prompt surface in the tree it measured (`prompt_surface_digests`, result schema
version 3), and the gate requires every surface this change is accountable for to
still hold the id that was signed. A squash reproduces the branch's files exactly,
so that survives it. Intersecting with the affected set rather than the whole map
is what keeps a merge legal — another PR's already-gated persona is not this
change's to answer for — while an edit the author makes after measuring fails. The
recorded `execution_diff_sha256` is still recomputed from the recorded base to the
recorded commit whenever history holds both, as a provenance check on the
evidence's own account of itself. `affected-run` refuses a dirty working tree and
pins the resolved commit into the signed diff, so the hash CI recomputes is the one
that was signed by construction rather than by coincidence.

Evidence also ratchets, and without that none of the rest was worth signing.
`evals/evidence/<case-id>/current.json` is a tracked file, so anyone with write
access to a branch could re-apply a prompt humans had reverted and restore, byte
for byte, the signed evidence that once measured it — both are public in the
history, and every other check passed by construction. A case's evidence may now
only move forward: its `started_at`, which is inside the signed payload, is
compared against the evidence for the same case on the base branch's tip.

The comparison is deliberately made of things the branch under review does not
choose. It reads `evals/evidence/` as a literal path rather than resolving the
`--evidence-dir` argument, because committing that directory as a symlink pointed
the comparison at a path no commit had ever held; and it compares at the base
branch's tip rather than at the fork point, because forking from before a case was
first measured left the fork point holding nothing to compare against. Both
silences were a pass. A symlink at or under the evidence directory is now refused
by name, and a comparison that cannot be made at all is
`evidence_ratchet_unavailable` rather than a shrug — this check is the only thing
between someone holding no key and evidence that looks current, so it does not get
to abstain.

Which commit is the tip turned out to be a demand on CI rather than a property of
the argument. `github.event.pull_request.base.sha` is the base branch as the event
saw it, and opening a PR before a revert lands pins it to the commit that still
carried the reverted prompt. That is worse than a quiet ratchet: the affected set
diffs from `merge-base(base, head)`, so a head restored to the pinned commit's
content carries no prompt surface at all and no case is selected, skipping every
check above. The workflow now resolves `origin/<base branch>` itself on a PR, and
keeps `github.event.before` on a push, where the branch's live tip is the commit
being gated. It fails the job rather than falling back, because everything
available as a fallback is a value the branch under review can influence.

The price is a tightening — a branch whose measurement predates another
measurement of the same case on the base branch is told to measure again, which is
the demand the 30-day expiry already makes and the one git already made by
conflicting on the single-line evidence file both branches write. What the base
tip changes is how soon it arrives: the fork point made the demand on the push
that resolved that conflict, and the base tip makes it on the branch's next CI run.
Both are before the merge button. The ratchet starts protecting a case once a
second measurement of it exists on the base branch: with none committed, there is
nothing to move backwards from.

Moving the evidence ratchet to the base tip and leaving the **coverage** ratchet on
the fork point left the same bypass with the key removed. Fork from a commit before
the case was written, edit only the prompt, and carry no case: at the fork point
there is no coverage to lose and no case to match, so the surface reports as
`coverage_debt` and exit 0 — no key, no signature, no evidence. The merge then
restores the case, because the branch never deleted it, and the push to the default
branch fails on `execution_prompt_surface_changed`. Green PR, red trunk, #402's
shape again.

Coverage and the registry are now compared against the base branch's tip too, and
what is judged is the state the merge would land: this branch's cases, plus what the
base branch has gained since the fork. The diff that decides *which surfaces this
change touches* stays at `merge-base(base, head)` — that is what all three merge
buttons land, and diffing from the tip is what made a release-scale PR structurally
unpassable (#367). A surface the base branch covers and this change does not is
`coverage_stale`, fatal, and distinct from debt: somebody did write that case, and
merging the base branch in and re-measuring is what clears it. The demand is scoped
to the affected surfaces, so a branch merely behind on a case it does not touch is
charged nothing, and a case the base branch added after the fork is not read as a
deletion. `affected-run` reports regressions, staleness and registry narrowings by
name instead of exiting 1 with an empty `failures` list, and a base tree the ratchet
cannot read is `coverage_base_unreadable` rather than a quiet pass.

Correcting the *cases* was half of it. Whether a surface is covered is not decided by
the case set alone — a persona is covered because some recipe references it and a case
binds that recipe — and that reference lives in the brick graph, which was read from
the branch's working tree and handed to the landing view unchanged. So the same bypass
survived one step further out: fork from before the base branch pointed a recipe at a
persona, edit only the persona, touch no recipe, and the branch's own tree honestly
reports that nothing reaches it. The merge restores the recipe the branch never
touched. The graph is now read at the base tip and at the fork point as well, and
merged edge by edge the way the case set is: `head | (base - fork)`, the monotone
half, which can only over-state what lands. Over-stating asks for a re-measurement
that the default branch's own push would ask for; under-stating is the bypass.

Read with `git ls-tree` and `git cat-file`, and that is the substance of it rather
than a note about cost. `git archive` renders a tree instead of reading it: it
applies that tree's own `.gitattributes`, so `export-ignore` deletes whole
directories from the output and `export-subst` rewrites the bytes of what survives
— and the same line in `$GIT_DIR/info/attributes` does it with nothing in any tree
and nothing in any diff. No flag turns that off for a tree-ish read. It made this
one reading the part of the gate that a single unremarkable line could switch off:
`.gitattributes` is under no surface prefix and is not the registry, so the PR
adding it reports `noop` and merges through ordinary review, after which both sides
of `base - fork` are missing the same edges, the difference is empty, and indirect
coverage stops being noticed on every branch, silently. The two readings beside it
were never exposed, because they already used `ls-tree`. A blob the read cannot
produce — `missing`, in a blobless clone — is the same named fatal as an unreadable
tree, and not a skipped file.

A `gate:` is a reference to a pattern, and the revision reader did not read that
field — so a base branch that gated a step on a pattern wired coverage the ratchet
could not see, and a branch editing only that pattern merged green while the
identical branch wired through `pattern:` was refused. 23 recipes here use `gate:`.
It is read now, with the same placeholder sentinel `build_brick_graph` uses, since
an ungated step spells its gate as an em dash rather than omitting the key.

That one was missed because it was looked for with the wrong instrument. Comparing
the two readers by counting edges per kind showed a *surplus* of `recipe -> pattern`
— duplicates from the field that was read outnumbered the 28 that were not — and a
total cannot show which edges are absent. The comparison is a difference of edge
sets now, and a test makes the answer to it a checked claim rather than a sentence:
every reference a recipe makes is modelled at a revision, and what is not modelled
is `agent -> persona`, `command -> instruction` and `wiki -> wiki`. Coverage that
reaches a surface only through those three is not ratcheted — such a change reads
as `debt`, not `coverage_stale`.

On a push to the default branch none of this changes what happens, as long as
`github.event.before` is an ancestor of what was pushed: the fork point is `before`
itself, nothing is added back, and both landing views are the pushed tree. A
force-push breaks that ancestry, and there the push is judged like any other divergent
history — a case the rewrite dropped is named instead of passed over.

The workflow step that runs it could not report any of that. The report exists only
on the command's stdout, redirected to a file, and the step's shell runs with `-e`:
a *failing* run aborted at the redirect, so the report was never printed and the
annotation block below it never ran. The `::notice::` for a widened registry was
reachable, because widening exits 0; the `::error::` lines, which exist only for a
failing run, could not execute at all. The step captures the status, prints the
report, annotates, and then exits with it. `coverage_stale` gets a line of its own,
because "merge the base branch in and re-measure" is not something an exit code can
say.

Three smaller things this made load bearing. `eval gate` and `affected-run` take
`--ratchet` and CI passes it: strict, a change touching one covered surface next to
any of the ~198 without a case failed `uncovered:<path>`, which no evidence can
answer, and `affected-run` refused to measure it at all. `git diff` runs under a
pinned configuration at both ends, because measuring on a laptop and recomputing on
a runner made `diff.noprefix` and `diff.renames` able to make the gate permanently
unpassable with the cause reported nowhere. And `RIG_EVAL_ATTESTATION_KEY` must be
64 hex characters: committed evidence publishes `key_id` on a public repository,
where a memorable passphrase is an offline guessing oracle ending in forgery by
someone who never held the key.

**The two ends of that key never held the same secret.** CI writes the 64-character
secret into a key file; a maintainer's key file held the 32 raw bytes Rig had
generated there. No secret is both, so `key_id` differed across the crossing and a
measurement signed on a laptop came back from CI as `invalid_evidence` — the whole
purpose of committing signed evidence, unusable, and unnoticed only because no
evidence had yet been committed to travel the route. The shape rule is unchanged;
what changed is that the hex is read as a notation for 32 bytes wherever it appears,
so the environment variable, CI's key file and the maintainer's key file denote one
key. A generated key file now holds that hex, which makes its contents the value to
paste into the repository secret. Key files from earlier versions keep signing and
need no regeneration: pair one with its hex spelling.

**And no case in this repository could be measured at all, because one filename
reads as a credential.** A result records a digest for every prompt surface, and
the keys of that map — paths from `git ls-tree` — were held to the rule written for
values, whose OpenAI-key pattern `sk-[A-Za-z0-9_-]{8,}` matches the
`sk-based-testing` inside `ri|sk-based-testing`. One of 202 paths therefore made
every measurement invalid, and it was judged after the providers had run twelve
times and the result had been signed, one line before the write. Digest keys are
now held to a path rule: escapes out of the tree, absolute and home-relative
paths, `file:` URIs and control characters are refused exactly as before, and the
secret-value scan — which can only ever be wrong about a public filename — is not
consulted. The detector itself is unchanged everywhere it guards an actual value.
The runner applies that rule to its argument before the first provider call rather
than after the last, since nothing about an argument improves while they run.

## [2.5.0] - 2026-08-11

**Review flows now speak before they finish exploring, and the orchestrator is held
to the same claim discipline it imposes on its reviewers.** Ten days of usage data
showed a third of sessions were security reviews abandoned mid-exploration with zero
findings emitted: the reviewers are subagents, so nothing reached the operator until
the barrier released, and the barrier never released. `parallel-review`, `pr-review`,
`security-audit` and `adversarial-review` now require a first report before dispatch —
capped at five tool calls, explicitly a preview rather than a verdict, and retractable
without penalty — and relay each verdict as it lands instead of only after aggregation.
The barrier itself is unchanged; judgment still happens at the gate.

`review-verdict` already forces subagents to carry evidence anchors and a stated
confidence, and `scan-anchors` verifies those anchors exist. The parent had no such
contract, and three retractions in a single day came from the same shape: asserting the
state of a CI check, a gate, a branch or a sensor scan that was never run. The
`orchestrator`, `goal-driver`, `implementer` and `talk-assistant` personas now name
those six subjects, require an explicit `未検証:` prefix for anything unverified, and
forbid writing an unrun scan into a gate record. This is prose, not a sensor, and each
persona says so.

`rig-wb hostcheck` gains two checks drawn from failures that actually stopped runs:
whether `gh` is authenticated with the scopes rig's own GitHub writes need, read off the
active account's stanza for the host this repo's remote actually points at, and whether
the *installed* `rig-wb` imports its subpackages from a directory outside any checkout
with `PYTHONPATH` unset — the exact condition under which a packaging omission hides
from every in-repo test. Both are injectable and carry `--bench` corpora whose negative
cases were confirmed against the pre-fix code. An axis that could not be verified
reports MISS, never OK: a token whose scopes `gh` will not print, a git invocation that
failed rather than a repository with no GitHub remote, and an editable install, whose
green would only ever have described the source tree it points at. `/rig:go` runs
hostcheck before each natural-language task and never blocks on it.

CI's prompt quality step is a ratchet now, like the structural step beside it. This
repository cannot satisfy it by any configuration — the `RIG_EVAL_*` secrets are unset
and the job installs only the Python package, so no provider executable exists on the
runner to run a case with — and failing anyway made every prompt-surface change red
with no action that could turn it green. Unmeasurable is debt: the affected cases are
named in a warning and in the run summary, and the step exits 0. A repository that has
a trusted lane is still enforced exactly as before.

`rig-wb wb context` judges what it measured against declared budgets and prints each
budget beside its verdict. The budget line and the report's heavy section are separate
thresholds — as one constant the verdict could only read `ok` when the section was
empty, which printed one fact twice. It still reports no dispatch rate: Claude Code
hands a subagent's shell the same environment and session id as the parent's, so the
signal does not exist, and the report states that rather than letting a reader assume
the axis was clean.

## [2.4.2] - 2026-08-11

**Codex hooks now follow Codex's JSON contracts instead of Claude Code's
PreCompact stdout convention.** The shared plugin resolves either `PLUGIN_ROOT`
or `CLAUDE_PLUGIN_ROOT`, so Codex no longer tries to execute `/hooks/*.sh` and
exit 127. Codex PreCompact returns valid no-op JSON; a compact-only
`SessionStart` hook then attempts a best-effort re-anchor from state retained in
the compacted context. It does not claim to reconstruct omitted state.
Claude Code retains its plaintext PreCompact instructions. Execution-level
regression tests cover both plugin roots and the rootless Codex-native mirror.

## [2.4.1] - 2026-08-11

**The research and examples that accompanied the Japanese-writing work are now
published with their provenance intact.** This patch adds three parity summaries
without the full candidate or reference prose. They retain the bounded judge reasons
needed to audit each preference, and contain only the repository's synthetic dev and
train cases. The patch also adds the historical Codex handoff that explains the
measurement caveats and the before/after/v2 Qiita-style Rig introduction examples.

It also publishes the exported `sales-playbook` skill. Its README now restores
credit and a link to the source channel, distinguishes the source material from
Rig's organization and wording, and makes the limits of the bundled MIT license
explicit. Claims supported by a single video remain marked for verification.

## [2.4.0] - 2026-08-11

**Technical articles can now use a dialogue structure without imitating a named
writer or inferring demographic traits.** The new
`styles/dialogue-tech-explainer` persona separates a reader's current question
from a specialist's bounded answer, advances one point per exchange, and falls
back to concise prose when the source has no genuine question or decision.
Its `dialogue-tech-writing` knowledge page captures the reusable structure—how
questions advance understanding, how code interrupts the rhythm, and when not
to use dialogue—while leaving facts, safety, register, and delivery format to
the other personas and policies composed in the same step.

Three generated Rig-introduction examples record the same draft under plain
generation, Japanese writing, and Japanese writing plus the dialogue persona.
They are examples rather than benchmark evidence; the short source intentionally
shows that the persona does not force dialogue where it adds no explanatory
value.

## [2.3.0] - 2026-08-09

**Independent Japanese-writing runs now have a real process boundary.** The
`secure-provider-execution` marker makes the Japanese pack fail before any provider
call or run-state write unless generator and verifier Claude/Codex executables are
pinned by reviewed absolute paths and SHA-256 digests (plus an explicit interpreter
pin for scripts). Runtime executes sealed verified bytes, transports prompts only on
stdin, fixes provider isolation flags and PATH, and filters credentials by vendor.
Opaque command providers cannot enter this lane.

Sensitive state, provider-output artifacts, and telemetry use owner-only directories,
descriptor-relative no-follow opens, 0600 fsynced temporary files, and atomic rename;
unverified symlink or hardlink targets are never truncated. The Japanese pack remains
at semantic version 0.4.0 but now requires engine >=2.3.0. Existing recipes without
the explicit secure marker, including `goal-loop`, retain their previous runtime path.

## [2.2.3] - 2026-08-10

**Every `rig-wb wb` subcommand was broken for anyone using the installed CLI.**
`rig_workbench/workbench/accept.py` reached a sibling `scripts/` directory through
`sys.path` to `import ast_diff`. In a checkout that resolves to `<repo>/scripts`;
installed it resolves to `site-packages/scripts`, which does not exist — and
`scripts/ast_diff.py` was never shipped, because pyproject's package finder only
includes `rig_workbench* / benchmarks* / skills* / packs*`. `accept.py` is imported
by `workbench/cli.py`, so `rig-wb wb <anything>` died at import with
`ModuleNotFoundError: No module named 'ast_diff'`; the only workaround was
`PYTHONPATH=<repo>/scripts` on every invocation. The module now lives at
`rig_workbench/ast_diff.py` and is imported as part of the package.
`python3 scripts/ast_diff.py <base.py> <new.py>` still works — that path is now a
launcher onto the same `main()`.

**`/rig:setup` never noticed a stale install.** The skip decision was "does
`rig-wb version` succeed", with no version comparison anywhere in
`scripts/install.sh`. A rig-wb installed in July sat at 1.6.0 while the repo moved
on, and every `/rig:setup` since reported "already installed ✓" — while the 1.6.0
launcher kept loading the current repo's `scripts/workbench.py` and failing with
`ModuleNotFoundError: No module named 'rig_workbench.workbench'`, which reads like
"rig-wb is not installed" rather than "rig-wb is out of date". The installer now
compares the installed version against this checkout's
`rig_workbench.__version__`, shows both, and **asks** whether to update. `--yes`
answers that prompt, `--force` reinstalls as before, and `--check` stays
detection-only — it reports the skew and installs nothing. Distribution is a git
ref, not a registry, so nothing here queries PyPI.

Three properties that comparison has to have, and did not:

- **What is compared is what is installed.** It compared the checkout and
  installed `${REPO_URL}@master` — a different artefact. On a branch ahead of
  master, accepting the update installed master, the checkout still disagreed,
  and the same prompt came back on the next run. A nag that cannot be satisfied
  teaches people to ignore it. Without an explicit `--ref` the install source is
  now the checkout itself (`pipx install <dir>` / `uv tool install <dir>` /
  `pip install --user <dir>` all take a directory), so accepting converges by
  construction. `--ref` still installs that ref from GitHub, and in that mode
  nothing is compared and no update is offered: this script cannot know a remote
  ref's version without fetching it.
- **Ordering, not equality.** "Different" was read as "older", so an installed
  2.3.0 against a 2.2.3 checkout was announced as `Update 2.3.0 → 2.2.3?` and,
  under `--yes`, performed unattended. A newer install is now reported and kept;
  `--force` remains the way to install an older checkout on purpose. `--yes` may
  skip the question but never the sentence naming what is being replaced and
  where the replacement comes from.
- **Unreadable is undetermined, not stale.** The version was the last whitespace
  token of whatever `rig-wb version` printed, which turned a non-zero exit into
  `?`, a leading warning line into its last word and a usage dump into `all` —
  each shown as an "installed:" version and each a fabricated mismatch. Only the
  two documented shapes (`rig-wb X.Y.Z`, or a bare `X.Y.Z`) are accepted now;
  anything else falls back to presence-only and replaces nothing.

**One resolver for `scripts/*.py`.** `rig_workbench/repo_paths.py` is now the only
place that answers "where is `scripts/<name>.py`" — `RIG_HOME`, then the install
source, then cwd and its parents. `orchestrate/mcp_scan.py` and
`orchestrate/commands.py` computed it from their own depth
(`parent.parent.parent`) and never consulted `RIG_HOME`, so an installed rig
looked for `site-packages/scripts/mcp_server.py` and reported #263 as "not
installed" even with `RIG_HOME` pointing at a checkout that has it. Resolving from
the package root removes the depth question instead of fixing two of its answers.

**The skew notice no longer quotes repo text raw.** `rig-wb`'s one-line
version-skew warning captured `__version__` with `[^"]+`, which spans newlines,
and printed it unprocessed — enough for a hostile checkout to put terminal escapes
and forged warning lines on rig-wb's stderr. The capture is bounded to one line
and goes through the injection scanner's `bounded_excerpt`, which now escapes
control characters as well as invisible ones.

The version bump is load-bearing rather than ceremonial: the broken build and the
fixed one would otherwise both answer `2.2.2`, so the new comparison — and
`rig-wb`'s own skew line — would skip exactly the installs that need replacing.

## [2.2.2] - 2026-08-08

**A Stop hook that interrupted sessions it had no business in.** The
instinct-learning reminder (`hooks/suggest-instincts.sh`, #306) returns
`decision: block` — it does not leave a note, it prevents the session from ending
and spends a full round-trip. Most sessions have no instinct worth recording, so
almost every firing is spent saying "nothing this time". Nothing about it was
sized for that cost:

- **It had no gate at all.** Its partner `inject-instincts.sh` opens with
  `[ -f "$WB" ] || exit 0`; this one checked nothing — not `.rig/`, not whether
  the session had touched rig. It fired in every session of every project that
  had the plugin installed.
- **Every failure path degraded to firing on every turn.** No session id,
  unparseable payload, an unwritable marker directory — each produced an empty
  parse result, which read as "not a recursive call, no id to de-duplicate
  against", so the reminder fired and kept firing. The de-duplication was the
  first thing to break and its failure mode was maximum noise.
- **The once-per-session marker lived under `$TMPDIR`**, so any environment
  handing out a per-invocation temp directory lost the guarantee silently.
- **The command it printed could not be run.** `python3 scripts/workbench.py
  instincts --add` is a repo-relative path that exists in no project that
  installed rig — which is every project this hook ships to.

Now it fires only when each precondition is affirmatively true: not already in a
stop-hook round-trip, a payload that parses with a session id and a readable
transcript, a transcript showing the session used rig, a runnable command to
suggest, and a marker it can actually write. Anything it cannot establish exits
silently. The no-session-id fallback is deliberately reversed — the old behaviour
fired anyway on the reasoning that losing the reminder is worse than repeating
it, which for a blocking hook means blocking every turn of the whole session.
The marker moved to `XDG_STATE_HOME`, and the suggested command resolves
`rig-wb wb instincts` first, falling back to the plugin-root path.

## [2.2.1] - 2026-08-08

**The gate could not see the file that governs every run.** #386 rewrote §6 of
`SKILL.md` and the prompt evaluation gate reported `noop`. Every root the surface
registry knew about — facets, patterns, recipes, agents, commands — is a
*subdirectory* of `skills/engine/`, so the two documents governing all of them
were the only prompt surfaces in the repository the analysis could not see.
Touching one line of a persona registered as affected; rewriting the section that
decides PARSE/RESOLVE/COMPOSE/RUN did not. That is the defect #384 fixed, pointing
the other way: there, a check fired on everything and distinguished nothing; here,
it did not fire on what matters most.

Registry v2 adds `skills/engine/` as a **non-recursive** root of kind `engine`,
covering `SKILL.md` and `PACKS.md`. Stated as a rule about the directory rather
than a list of two filenames, so a third engine document does not silently reopen
the hole. Registered subdirectories still win, so a recipe stays `recipe:bugfix`
and never becomes `engine:recipes/bugfix`; `corpora/` stays out, being drill
fixture data the gate consumes rather than prose the model reads. Case ids may now
declare `engine:` bindings — without that half the debt would have been unpayable,
which is a permanent warning rather than a ratchet.

This could not have shipped before the ratchet. Under `--require-cases` the first
change to `SKILL.md` would fail with no way to pass, because the case covering it
is itself a change to a prompt surface. As debt it is counted, named, and exit 0.

**And the registry itself is now monotonic**, which the above change forced.
Editing `evals/prompt-surfaces.json` was fatal outright, on the reasoning that
changing what the gate can see is not a coverage question — true, and the effect
was that the registry could never be extended without failing the job. #383's shape
again, aimed at the one change class that *widens* coverage, and unpassable by
construction since no eval case can be written for a registry. Now: widening a root
passes with a notice; removing one, renaming its kind (which orphans every case
bound to the old ids without deleting a case, so `coverage_regressions` cannot see
it), or dropping its extensions or recursion is fatal. A base tree that cannot be
read still accuses nobody.

## [2.2.0] - 2026-08-08

**A rule stated 152 times and never once counted.** `context-minimal` is called a
hard rule in SKILL.md §6, and nothing in this repository measured a byte of it —
which is two of the holes rig's own `harness-taxonomy` names, at the same time:
enforcement that stops at prose, and a rule shipped without measurement. The
existing token metering only ever covered HTTP providers, and `/rig:go`'s default
manual backend never reaches `orchestrate` at all, so the main path wasn't even in
`runs.jsonl`.

Every byte a rig command prints returns to the parent as a tool result, so **rig's
stdout is rig's contribution to the parent's context**. That is the part rig is
responsible for and the only part it can observe, so that is what `workbench.py
context` counts — per invocation, into `.rig/context.jsonl`, with the scope stated
in the report itself: not the session's total context, not the conversation, not
files the parent opened on its own, and not whether the parent actually dispatched
to a subagent. A number claiming to be "your context usage" would be a fabrication.
`RIG_NO_CONTEXT_METER=1` turns it off.

This landed *first*, and deliberately: everything below adds output, and adding
output to an unmeasured area is precisely what the doctrine forbids.

**What the recipe is going to do, shown to somebody who doesn't already know.**
The registration banner named the chosen recipe and stopped. `bugfix` is seven
steps, fans out to three reviewers at step six and judges fifteen criteria at step
seven; the information existed the whole time in `orchestrate plan`, one command
away from the path anybody takes — an asset present and not connected.

- `new` prints the flow map, with `◆` on the steps that are hard stops and the
  final gate's criterion count.
- **Shape decides the display.** Twelve shipped recipes have exactly one step, and
  `[▸] 1/1` is a progress bar over a single item. Those show their fan-out and gate
  instead of a position — what's complex about them is inside the step.
- Each step transition prints position and what's next (~7 lines a run, not per
  turn). A retry shows `↻` and its attempt separately from the position, because a
  bar that won't move reads as a stuck run.
- `board` gains the bar, the position, and a **whose-move-is-it** column in one
  vocabulary — plus the `あなた待ち / 他人待ち / 実行中` footer that is the only
  thing that makes a multi-task board glanceable.

The denominator is real: `steps.json` is seeded from the resolved recipe at
registration, and records *that it was seeded*. An unseeded run grows its step list
from whatever gets reported, so deriving a denominator from list length would
announce "1/1, all steps complete" about a run whose step count nobody knows. The
seeding fact is read, not inferred, and runs registered by an older rig keep the
previous display rather than being handed a number nobody measured. Throughout,
the step list is display metadata and never an input to the accept decision — that
stays with the acceptance gate.

**`queue go` now says what the batch left on your desk.** `3/4 done` describes the
queue's bookkeeping: `DONE` means the gate settled and the verifier passed, which
is neither "merged" nor "nothing left to do" — each of those tasks is in its own
worktree waiting for a person. The tally is followed by the batch regrouped by the
move each item waits on, in `board`'s exact wording. Linking is evidence-based: a
queue item becomes a workbench task inside the provider's own session, so an item
whose id can't be recovered is listed as unlinked rather than bucketed on a guess.
In a screen whose job is "which of these needs me", a wrong attribution is worse
than an admitted gap.

**Queue depth moved to `cockpit`, not the status header.** Backlog depth is
something you go and look at; the parent session's context is the budget
`context-minimal` protects, and it is now a number that moves. An unreadable queue
store is reported as unreadable, never as an empty one — the same stance #360
established, because "0 queued" and "the backlog file is broken" must not look
identical on a dashboard.

## [2.1.1] - 2026-08-07

**The prompt evaluation gate was red on every change that touched a prompt
surface, so it was merged past.** `--require-cases` demands that every affected
surface already have an approved evaluation case. `evals/cases/` is empty, and an
approved case can only be created by `eval promote` from a measured red→green run
pair — so the requirement failed every prompt-surface change, *including the one
that would add the first case*. #383 merged over it. #381 merged over it. A check
that fires on everything distinguishes nothing, and what it actually teaches is
that this job gets merged past — a habit that does not stay confined to one job.

The requirement was right; expressing it as a threshold was not. `eval affected
--ratchet` states it as a direction instead:

| affected surface | `--require-cases` | `--ratchet` |
|---|---|---|
| has a case | pass | pass |
| has no case yet | `uncovered`, exit 1 | **`coverage_debt`**, exit 0 |
| its coverage was removed here | not detected | **`coverage_regressions`**, exit 1 |
| kind not in the registry | `uncovered`, exit 1 | `uncovered`, exit 1 |

Debt is reported, never swallowed — each path, the commits that touched it, and a
GitHub warning annotation with the count. What cannot happen is coverage going
*down*: deleting a promoted case, or narrowing one's `prompt_surfaces`, fails the
job. Coverage is monotonic, which is the same rule the governance layer applies to
policy layers, and the debt count is a number that moves from the first day rather
than a wall that never opens.

Two details that keep the new check honest:

- **A regression is only claimed when it can be demonstrated.** If the base tree
  cannot be read — shallow clone, unborn ref — the comparison reports no
  regression rather than accusing the change of having deleted everything.
- **The paid quality steps now key off `affected_cases`, not off the status.**
  With no case covering the change there is nothing to measure, and demanding a
  provider run anyway was part of what made the gate unpassable.

`--require-cases` keeps its exact previous meaning and is still available; it is
what this repository should switch back to once the corpus covers the surfaces
that matter. Nothing about how cases are captured, promoted or run changes.

## [2.1.0] - 2026-08-07

**Governance reaches any stage, not only accept.** v2.0.0 governed the one place
changes enter the working tree, which is the right first place and not the only
one a team needs a person in the loop. An architecture decision, a release
sign-off or a data-migration review has to happen *at that stage* — approving it
retroactively at the end is not the same review.

The recipe schema was already a workflow DSL: `steps[]` carry `gate`,
per-stage `acceptance`, `max_retries`, `needs` (DAG), `condition` and `checks`,
and `orchestrate` runs them as a deterministic state machine. What it could not
do was *park* a run — halt at a named stage, stay halted across processes, and
resume when a qualified person signs off. Two step fields and one state add that,
and neither invents a mechanism: both reuse v2's approval arithmetic unchanged.

- **`steps[].human_gate`** — `true`, or `{quorum, roles, separation_of_duties,
  expires_hours}`. After the machine gate passes, the step moves to
  `awaiting_approval` instead of advancing. Quorum, qualifying roles,
  **separation of duties** (whoever ran the stage cannot sign it off — `ran_as`
  is stamped at START for exactly this) and **freshness** (bound to the approved
  commit) are govern.approval's, not a second implementation.
- **`steps[].actor`** — the org **role** that owns the stage, as distinct from
  `personas`, which are the LLM personas that do the work. It seeds the human
  gate's approving role. It deliberately does **not** block execution: rig cannot
  verify that a human architect typed anything, only that one signed, and
  refusing to run would break every CI-driven pipeline for no safety gain. An
  execution outside the owning role warns at START and lands in the run history.
- **`approvals: {"stage:<step-id>": …}`** in a policy — the org's half. A stage
  becomes gated because the policy names it, not because the recipe author
  remembered to. Recipe and policy merge to the **stricter** rule (higher quorum,
  union of roles, shorter expiry, separation of duties if either asks), so a
  recipe can never talk the org down. Stage keys tighten downstream between
  policy layers like every other field.
- **`orchestrate approve <step-id> [state.json] [--deny] [--note …]`** releases a
  parked run, or records an objection. Decisions land in the run-state beside
  that step's checks and verdicts, and in the tamper-evident ledger as
  `stage.approve` / `stage.deny`. A later approval supersedes an earlier denial
  from the same person — people change their minds, and two contradictory records
  would make the quorum arithmetic meaningless.

`next`, `resume` and `approve` exit **3** when a run is parked, and `run` exits 3
when it finishes parked: waiting for a person is not a failure, and reporting it
as one (or as success) is wrong in both directions.

Also fixed: `next` and `resume` exited **0** on the transition that BLOCKED a run,
and only exited 2 the *next* time that state was loaded — so a blocked run
reported itself successful for exactly one invocation. Both now exit 2 at the
transition.

**Nothing changes for a recipe that declares neither field**, and the identity
lookup is skipped entirely in repositories with no `.rig/org.json` and no step
that declares an owner — no policy load, no `git config` subprocess per step.

## [2.0.0] - 2026-08-07

**rig becomes an AI Quality Operating System.** Everything v1 does — the
acceptance gate, isolated worktrees, independent verification, force-proof
accept — was built for one person and one repository, and in that shape it is
finished. Nothing about it changes here. What v2 adds is the layer above it:

```
team A ─┐
team B ─┼─→ common policy ─→ permissions → approvals → waivers → audit
team C ─┘
```

Four things break the moment the same setup is handed to three teams, and each
one becomes a first-class concept rather than a convention:

- **Policy** (`.rig/policy/*.json`, stacked org → team → project). `.rig/gates.json`
  is per-repository, so a criterion team A added never reaches team B — "we run a
  common policy" stays a claim. A policy document is versioned, shared (one
  checkout via `$RIG_POLICY_HOME`, referenced by the same relative path from every
  repository), and stacks under one rule: **monotonic tightening**. A downstream
  layer may add criteria, raise a quorum, shorten a waiver, narrow a role. It can
  never drop a criterion the org requires, lower a quorum, extend an expiry, or
  hand a role a permission the org never delegated — each attempt fails naming the
  layer and the field. Omission does not lose a criterion either: layers union
  rather than replace, so both ways of quietly dropping the org's rules are closed.
  `rig-wb govern policy lint` is the check; it exits 3 when a layer loosens.
- **Permissions** (roles → a fixed 11-permission vocabulary → actors). v1's
  `.rig/access.json` is an allowlist for exactly one permission, so in practice a
  team either gives everyone `--force` or gives it to one person who then becomes
  the bottleneck. `accept`, `accept.force`, `approve`, `waiver.grant`,
  `policy.publish`, `audit.export` and the rest are now separable. Denials always
  say who *does* hold the permission — a permission system nobody can read is one
  people route around.
- **Approvals** (`.rig/runs/<id>/approvals.json`). "Someone reviewed it" cannot be
  checked afterwards. An approval is now a stored decision with a quorum,
  qualifying roles, **separation of duties** (the author's own approval never
  counts) and **freshness**: it is bound to the commit it approved and to a
  wall-clock expiry, so an approval that survives a force-push stops counting. That
  binding, not the quorum number, is what separates an approval flow from a rubber
  stamp.
- **Waivers** (`.rig/waivers.json`). v1 recorded a `--force` but could not tell
  "the quality owner signed off on shipping this until Friday" from "somebody was
  tired at 19:00". A waiver names the criteria, the reason, the owner and the
  expiry; under a policy that sets `required_for_force`, `--force` without a live
  one is refused. Criteria listed as `non_waivable` (the scaffolded default:
  `no_secret_leak`, `no_gate_tampering`, `no_destructive_operation`) cannot be
  covered by any waiver at all.
- **Audit ledger** (`.rig/ledger.jsonl`). `.rig/audit.jsonl` is append-only and
  trivially editable — delete last Friday's override and the record says it never
  happened. The ledger hash-chains every entry to its predecessor and HMAC-signs it
  with the repository's existing `.rig/provenance.key`, so editing, deleting,
  reordering and key-less forged appends are all detected by
  `rig-wb govern audit verify`. `.rig/audit.jsonl` keeps its v1 shape, so
  `workbench audit`, `digest` and every existing reader are untouched.
- **Conformance** (`rig-wb govern conformance`, `rollup`). Nine checks measure a
  repository against its effective policy — is an org layer actually reaching it,
  do policy-required criteria appear in the gates of accepted runs, were the
  approvals real, is the ledger intact, and the single most informative number in
  the whole layer, the **force rate**. `rollup --scan` aggregates several
  repositories into the per-team table the picture above describes. Both exit 3 on
  a failing check, so CI can gate on them without parsing output.

Enforcement adds no new choke point. `accept` was already the only way into the
working tree; it now asks four more questions before the squash merge — may this
actor accept, is the approval requirement met, may they force, and is every
bypassed criterion covered by a live waiver — and a refusal leaves the tree
untouched. Two choke points would have meant one of them has a way around it.

**Nothing changes for solo use.** With no `.rig/org.json` the governance layer is
inert: no output, no checks, no new files. `.rig/access.json` and `.rig/gates.json`
keep working exactly as before and are honoured *alongside* a policy, never
replaced by it; `rig-wb govern migrate` folds them into a policy layer when a team
is ready, leaving the originals in place. The one deliberate behavioural difference
is fail-closed: where a malformed `.rig/access.json` falls back to unrestricted
(the safe side for one person), a policy layer that does not parse blocks `accept`.
A misplaced comma silently costing an org its rules is the one failure this layer
cannot have.

The major version is for the concepts, not for a break — no v1 command, file or
flag changed meaning.

New: `rig-wb govern init|migrate|policy|whoami|can|approve|waiver|audit|conformance|rollup`,
the `/rig:govern` pack (`governance-auditor` persona, `quality-operating-system`
knowledge, `govern` instruction, `conformance-report` output-contract, `org-policy`
policy facet, `govern-audit` recipe), `RIG_POLICY_HOME` and `RIG_ACTOR`.
Gates built under a policy carry the required criteria with `origin: "policy"`,
alongside the existing `origin: "project"` from `.rig/gates.json`. Tasks record
`actor` (and `org`/`team` when bound) so separation of duties has an author to
compare against.

## [1.36.0] - 2026-08-07

Two things that had the same shape: a prompt that fired when it had nothing to say, and
a pack that had judgment with nothing to judge.

- **The instinct-suggestion Stop hook now fires once per session instead of every
  turn.** The reminder blocks the stop, so repeating it costs a full round-trip each
  time — and the hook's own comment says most sessions have nothing worth recording,
  which makes almost all of those round-trips a turn spent saying "nothing this time".
  Observed in a live session: 16 firings, 6 of which produced an instinct; the other 10
  were pure overhead crowding out the work. A marker keyed by `session_id` under
  `$TMPDIR` enforces the limit; clients that send no `session_id` still get the
  reminder every time, since losing it entirely is the worse failure. The id is folded
  to `[A-Za-z0-9_-]` before use, so it cannot escape the marker directory — dots
  included, because an id of `..` would resolve to a directory that always exists and
  would silence the hook permanently.
- **The sales domain pack now ships a knowledge layer, and its personas reference it.**
  All seven reviewers carried their evaluation axes and nothing to apply them to:
  `facets/knowledge/sales-domain/` held two blank templates, and not one persona in
  `packs/` declared `inject:`. Eleven canonical wiki pages now sit at
  `packs/domain/sales/facets/knowledge/` — prospecting, first impression, discovery,
  closing, price negotiation, follow-up, referrals, scripts and practice, mindset and
  pipeline hygiene, goal management, and team coaching — and six personas inject the
  ones that match their axis. Nine of the eleven are wired that way; `sales-goal-management`
  and `sales-management-coaching` are addressed to a team lead, and the pack's reviewers
  all judge a single deal, so nothing injects them — they ship as reference pages a user
  can `inject:` into their own persona, reachable from `sales-mindset-antipatterns`'s
  `links:`. Adding a coach persona would have dragged a second output contract in with
  it, since `deal-verdict` is bound to the five deal-review 観点.
  The axes stay in the persona (that is the judgment half);
  the material moves to the wiki. Each persona's existing 自社固有
  `knowledge/sales-domain/` sentence is kept distinct from the new generic pages: two
  different knowledge sources, deliberately not merged.
- **`inject: [[slug]]` now resolves against installed packs, and SKILL.md says so.**
  §5's tier table listed project overlay, global, org, and shipped — a pack had no way
  to ship a page its own persona could reference, which is why `packs/` had zero
  `inject:` up to now. The pack tier sits between org and shipped, and a pack's pages
  live at `facets/knowledge/<slug>.md` so the slug stays bare and `[[slug]]` reads the
  same everywhere. `facets/knowledge/_wiki.md` and `facets/instructions/validate.md` carry
  the same row — the latter matters because ⑤ wiki 衛生 enumerates the tiers it searches,
  and leaving packs out of that list would have reported every pack persona as 参照欠落.

## [1.35.0] - 2026-08-07

Removes the top-level `bin/` directory. It cost this plugin two entire surfaces to
buy one shell convenience that three other entry points already provide.

- **`bin/orchestrate` is gone, and with it the `bin/` directory.** 1.28.2 found that a
  top-level directory *named* `bin/` makes Cowork's marketplace sync fail outright —
  independent of the file inside it, its contents, or its executable bit; renaming a
  byte-identical copy fixed it immediately. That entry chose to keep `bin/`, on the
  grounds that it backed a documented Claude Code feature (plugin `bin/` added to
  `PATH`) and the fault looked like a client bug worth reporting rather than
  designing around. The same symptom has since shown up in Claude Desktop, which
  changes the arithmetic: one convenience is not worth two surfaces where the plugin
  cannot be installed at all.
  What is actually lost is `orchestrate` appearing on `PATH` by itself after a plugin
  install. Everything that used it keeps working — `python3 scripts/orchestrate.py`
  is the path SKILL.md already calls, `rig-wb` covers the pip install, and
  `.claude-plugin/bin/rig` (via `orchestrate install-shim` → `~/.local/bin/rig`) is
  the same wrapper by a different route. SKILL.md's fallback for "the `orchestrate`
  command is not found" has been in place since the shim was introduced, so the
  degraded path was already exercised.
- **`test_no_top_level_bin_directory` keeps it from coming back.** Nothing else in the
  repo would notice a re-added `bin/`: the plugin still builds, installs on the CLI,
  and passes every other check — it just quietly stops being listed. Proven
  non-vacuous by creating the directory and watching the check fail, then removing it
  and watching it pass.
- Docs in both languages now describe the removal and point at `install-shim` instead
  of promising `orchestrate` on `PATH`.

## [1.34.0] - 2026-08-07

Fixes a gate that was blaming pull requests for work they had not done, and
gives two measured things a way to be seen.

- **The prompt evaluation gate charged a branch for the base branch's changes**
  (#367). `eval affected` diffed against the base *tip*, which on a branch that
  forked a hundred commits ago includes everything the base branch did since —
  in reverse. Those files read as changed by the branch, so the gate demanded
  evaluation cases for prompt surfaces the author never opened, and no amount of
  work on the branch could clear them. It now compares against the merge base,
  which is what "this branch changed" means. Proved on a fixture first: a branch
  touching one command, with the trunk moving on a recipe meanwhile, reported
  both files before and reports one now.
  The fork point used is reported as `merge_base` so a surprising result can be
  checked rather than guessed at, and each blocking path now names the commits
  that touched it — a large PR gets a triage list instead of a wall of paths.
  A branch that genuinely rewrites the prompt layer still cannot pass, and
  should not; what is fixed is the inflation, not the requirement.
- **`rig-wb runs --auto-route-regret`** (#357). `learned_auto_route` has always
  aggregated per-model pass rates to decide the *next* route, and never shown
  that aggregate to anyone. Choosing a cheaper tier is a bet, and with no way to
  see it settled there was no telling a saving from a false economy. This prints
  each candidate's attempts and pass rate per routed step and flags a **possible
  regret** when the chosen model is below the quality bar and a pricier
  candidate with enough observations passes more often. A regret needs both
  sides to have earned an opinion: a lucky single expensive run does not convict
  the cheap tier, and a cheaper model doing *better* is not a regret. Read-only
  over `.rig/runs.jsonl` — no writes, no model calls, no change to routing.
- **README: `rig-wb bench-invariance` was undiscoverable** (#353). The feature
  that produced the most consequential finding in this repository — that rig's
  safe_rate tied bare's on `trusted-helper-authz`, because a verifier that
  cannot see a defect does not start seeing it when the loop retries — appeared
  nowhere in either README. Added as Claim C alongside the two existing claims,
  with `agreement` and `safe_rate` defined and the panel's actual result stated
  rather than the capability alone.

Suite 1489 → 1510 passed.

Not attempted: CLI provider session reuse (#326). Its own acceptance criteria
require verification against the real CLIs, and this change set had no way to
run them; shipping it on mock evidence is the thing that issue asks not to do.

## [1.33.0] - 2026-08-07

Closes the gap between what `validate.md` says the validator checks and what it
actually checked. Every item here was already specified — some since #14 and
#104 — and had no executable counterpart, so a manifest or recipe could violate
the written rule and pass in silence. The backlog had been accumulating one
auto-filed issue per gap; this clears eight of them.

- **manifest** (`default_max_retries`, #360) integer ≥1, with `true` rejected
  rather than read as 1 through bool's int inheritance.
- **manifest** (`default_recipe` / `default_personas[]`, #372) now resolve
  through the same project→user→shipped resolver COMPOSE uses. Both failed
  silently before: an unresolvable recipe dropped RESOLVE into interactive mode,
  and an unresolvable persona was dropped from the review fan-out, so a typo
  cost you a reviewer with nothing printed. `interactive` stays reserved.
- **manifest** (`knowledge.context_file` / `adr_dir` / `design_docs[]`, #363)
  are checked for existence and **WARN**, not FAIL, per the #14 spec — a missing
  knowledge path costs the run context, not correctness, which is exactly why it
  goes unnoticed. WARN and FAIL are collected separately so neither hides the other.
- **recipe step** (`model` / `verifier_model`, #362) must be strings. A
  non-string reached the provider as argv and failed at subprocess time; an
  empty string is falsy, so the provider quietly used its default and the
  recipe's explicit choice vanished. Empty warns, non-string fails.
- **recipe step** (`auto_route.candidates`, #358) schema plus cheapest-first
  ordering. Selection takes the first candidate whose `max_size` covers the
  current size, which makes declared order part of the behaviour: an
  out-of-order list routes to a costlier model than the recipe reads as, and an
  unrecognised `max_size` defaults to XL and wins every route.
- **catalog drift** (#364) now scans `patterns/`. It never did, so
  `patterns/failure-taxonomy` — referenced by another pattern, wired into
  run-report, used by three modules and covered by its own tests — stayed off
  the §2 catalog with nothing able to notice. Both it and
  `facets/instructions/adaptive-assess`, which the widened scan then surfaced,
  are now listed; catalog drift reports zero missing entries.
- **accumulated/** (#365) frontmatter (`category` / `title` / `date`) and the
  two required body sections, spec'd in #104 and #203 and never implemented.
  WARN only, and silent when the directory is absent. A date left unquoted in
  YAML arrives as a date object, which is the format the spec asks for, so it is
  accepted rather than reported as the wrong type.
- **did-you-mean for recipe names** (#188). An unresolvable `--recipe` printed
  the search paths and nothing else. It now offers the near misses, by edit
  distance for a misspelling (`hotfixx`, `release_flow`) and by substring for an
  abbreviation (`review` → `review-only`) — the issue's own example, which edit
  distance alone does not reach. Suggestions carry their tier and come from the
  directories just searched, so one can always be run as printed.

The validator's selftest gains 8 scenarios (36 total) and `tests/` gains 63
checks. `scripts/validate.py` on this repo: PASS 41 / WARN 4 / FAIL 0, one WARN
fewer than before because catalog drift is now clean.

Not done: the "CI 実装状況" note in `validate.md` still lists four of these as
unimplemented. Correcting it edits a registered prompt surface, which the
prompt evaluation gate blocks without evaluation-case evidence from a paid run
(#367). The prose specifying each check is unchanged and was already right —
only the status note is stale.

## [1.32.0] - 2026-08-07

Makes the mutation adapter a rig command. 1.31.x shipped it as a loose script, so
the one detection surface an operator had to reach into `scripts/` for — naming the
format and the path by hand — was the same surface `hostcheck`, `coverage` and
`asvs` already exposed as `rig-wb` subcommands.

- `rig-wb mutation` replaces `python3 scripts/mutation_adapter.py <format> <report>`.
  With no arguments it finds the report in the places Stryker and mutmut write one
  and reads the format out of the file rather than off the filename, so a report
  saved under an unexpected name still parses and one whose name lies about its
  shape does not fool the parser. A candidate that turns out not to be a mutation
  report is skipped rather than scored. Verified against both throwaway projects
  from 1.31.1: 80.8% (Stryker) and 40.9% (mutmut), the numbers those runs already had.
- `--run` runs the project's own mutation tool first, detected from its configuration
  — `stryker.conf.*` or a `@stryker-mutator` dependency, `[mutmut]` in setup.cfg or
  mutmut named in pyproject.toml. Detection demands that a marker actually name the
  tool: guessing wrong means running a long job the project never asked for, so a
  bare `pyproject.toml` is not consent. rig still does not do mutation testing itself.
- Fixes `--apply`: it recognised one workbench failure string and printed
  `applied ...` with exit 0 for every other one, so a task id that did not exist
  looked like a criterion that had reached a gate. It now follows the exit code.
- `scripts/mutation_adapter.py` remains as a deprecating shim that forwards to the
  new command, and the old positional form is still accepted, so 1.31.x instructions
  keep working.
- Not done: a `/rig:` entry point. `commands/` is a registered prompt surface, so
  adding one requires evaluation-case evidence (red/green plus a clean control)
  before it can land. Stated here rather than half-wired. The coverage map is
  unchanged: `detection-power` asks that the suite's detection be measurable, which
  it is — where the entry point sits is ergonomics, not evidence.

- Verified the `elements` path against a real Stryker report (9.6.1, command runner,
  26 mutants on a throwaway project). No fix was needed: the adapter's score matched
  Stryker's own in all three states — 53.85% with a weak suite, 88.46% after adding
  boundary and error cases, 80.77% once three boundary assertions were deleted, which
  the baseline comparison reported as a warning while `npm test` stayed green. Adds a
  regression test built from the real report's shape, since a live report carries
  fields (`killedBy`, `mutatorName`, `statusReason`, `testsCompleted`, and top-level
  `config` / `framework` / `testFiles` / `thresholds`) that the synthetic fixtures did
  not. This closes the "no real Stryker report has been parsed" caveat from 1.31.1.

## [1.31.1] - 2026-08-07

Fixes the mutmut integration 1.31.0 shipped, which was written against a command that
no longer exists. Found by running it on a real project instead of a synthetic report —
the one check 1.31.0 recorded as unverified.

- `mutmut junitxml` is mutmut **2.x**. Version 3.x dropped it and writes
  `mutants/mutmut-cicd-stats.json` via `mutmut export-cicd-stats` instead: a counts
  summary, not one test case per mutant. `mutation_adapter.py` gains a `mutmut` format
  for it and keeps `junit` for 2.x and other JUnit producers, so the adapter does not
  force a version upgrade on the project using it.
- The new format maps `timeout` to detected and `no_tests` to undetected, and excludes
  `suspicious` / `segfault` / `skipped` / `check_was_interrupted_by_user` as invalid — a
  mutant that confused the run is not a hole in the suite. If the counts do not add up to
  the report's own `total` it refuses to score, rather than working from a short
  denominator that would inflate the result.
- Verified end to end on a throwaway project: 22 mutants, weak suite 22.7%, strengthened
  suite 68.2% (passed), boundary assertions deleted 40.9% (warning) — with `pytest` green
  in all three states, which is the case mutation testing exists to catch. The criterion
  reached the acceptance gate through `.rig/gates.json` `extra_criteria` and was recorded
  as a warning by `--apply`.

## [1.31.0] - 2026-08-06

Measures two things rig previously asserted, and states plainly where its security
inspection surface ends.

- `rig-wb hostcheck --bench` measures the host checks against a fixed corpus of 23
  cases instead of against whatever machine runs them: every case supplies its own
  environment, so the numbers mean the same thing on a laptop and in CI. The negative
  cases are the point — a committed `devcontainer.json` with no container around the
  session, an allow-list with no deny rules, a commented-out `.rig/` ignore. Current
  corpus: 11/11 detected, 0/12 false positives. `check_isolation` now takes its
  environment and signals as arguments, which is what made it measurable.
- `scripts/mutation_adapter.py` brings the other half of detection power into the
  gate. `/rig:drill` scores a reviewer; this scores the test suite, by ingesting a
  report from a tool that already does mutation testing — the mutation-testing-elements
  JSON that Stryker emits, or the JUnit XML from `mutmut junitxml`. Timeouts count as
  detected, no-coverage counts as undetected, compile and runtime errors are excluded
  as invalid rather than held against the suite. The criterion is comparative and
  **warning-grade by design**: equivalent mutants make the absolute number unreachable,
  so only a drop against the recorded baseline is actionable. Declare
  `mutation_score_not_regressed` per project via `.rig/gates.json`.
- `rig-wb asvs` maps the ASVS chapters against the inspection surface rig actually has.
  Of 17 chapters, 2 are backed by a measured sensor or drill class, 9 partially, and
  **6 have nothing at all** (web frontend, session management, self-contained tokens,
  OAuth/OIDC, secure communication, WebRTC) — stated as blind spots rather than
  omitted. `--check` verifies every cited sensor, reviewer and drill class still exists
  and runs in CI, so the map cannot outlive its references.

## [1.30.0] - 2026-08-06

Makes the coverage claim checkable instead of asserted, and gives the two
requirements that had no home in rig one.

- Adds `rig-wb coverage`: a map from each documented requirement to the evidence
  behind it (`evals/coverage-map.json`), with status derived rather than stored —
  `measured` only when deterministic evidence runs here, `partial` when something
  is still planned or needs a paid run, `declared` when the mechanism exists but
  its effect is not measured. The default mode verifies the map against the tree
  (every cited path and allowlisted command must exist) and now runs in CI, so a
  claim cannot outlive the evidence it names. `--run` executes the deterministic
  evidence; all of it passes today.
- Adds `rig-wb hostcheck` for the two prerequisites rig cannot enforce itself:
  whether the session is actually bounded by a container, whether the host
  permission layer denies anything, and whether run state is kept out of version
  control. Detection only — it reports and exits 3, and rig still runs.
- Ships two operator templates: `docs/templates/devcontainer.json`, whose
  `remoteEnv` marker is the one hostcheck looks for, and
  `docs/templates/rig-scheduled.yml` for the trigger side of a proactive loop,
  which rig's in-session scheduler cannot provide. The scheduled template defaults
  to `auto_pr: false` and serialises overlapping runs.

## [1.29.0] - 2026-08-05

- Moved the movie and scenario workflows into the opt-in `video-storytelling`
  domain extension. It ships pack-local language and content-risk reviewers,
  concrete structural evals, and resolves only after explicit installation.
- Moved the sales review and enablement workflows out of default core into the
  opt-in `sales` domain extension. Its recipes and catalog command become
  resolvable after an explicit project install; pack installation does not claim
  to register a host slash command automatically.
- Moved the niche `sns-x` workflow out of the default core catalog into the opt-in
  domain extension at `packs/domain/sns-x`. Install it per project with
  `rig-wb pack install domain:sns-x --scope project --allow-unverified`.
- Replaced the core scenario workflow's X-specific reviewer dependency with the
  medium-neutral `content-risk-reviewer`.

## [1.28.3] - 2026-07-31

Calibrates the Japanese natural-writing discriminator with its first positive control.
The 31-pair/two-order design responds monotonically, but its observed resolution is much
coarser than earlier conclusions assumed: 19.4 percentage points when treating pairs as
the unit and 29.0 points at the correct eight-article cluster level. The recorded
0--3.3-point changes therefore do not establish zero effect; they establish only that no
large movement was observed in this sample. A human-vs-human endpoint also lands at
69.4%, showing that the raw 50% target is not calibrated across the two article pools.

- Adds `mde_calibration.py`, paired/article-cluster analysis, sample-size lower bounds,
  and the 434-judgment positive-control record.
- Adds resumable, input-bound checkpoints to `discriminate.py` and makes an empty fetched
  human corpus fail loudly.
- Publishes only anonymous pair outcomes and derived numeric results. Fetched article
  bodies, source item identifiers, body hashes, URLs, and judge commentary remain local
  and are not committed.
- Revises the benchmark documentation to distinguish "not detected" from "no effect"
  and records which earlier conclusions survive the calibration.

## [1.28.2] - 2026-07-30

Documents the actual root cause of rig failing to appear in Cowork's plugin browser —
no code or manifest change in this repo, just the corrected record. See the correction
note under 1.28.1.

Found by controlled bisection: cloned rig's tracked HEAD content (no git history) into
a throwaway repo, then repeatedly halved it, force-pushing and re-testing in Cowork
after each cut. Repo byte size, file count, and commit count were each tested in
isolation first (a 20MB blob, 700 tiny files, 320 trivial commits) and none reproduced
the failure on their own — ruling out rig's overall bulk (86MB / 309 commits) as the
cause before the content bisection even started.

**Root cause: a top-level directory literally named `bin/` makes Cowork's marketplace
sync fail outright**, independent of the file(s) inside it, their content, or their
executable permission bit. Renaming `bin/` to anything else, with byte-identical
contents, fixed it immediately. This repo ships `bin/orchestrate` to use Claude Code's
documented plugin `bin/`-on-PATH feature (`orchestrate` callable from a shell after
install) — a legitimate, intentional plugin construct, not something to remove to work
around a client bug. Left in place; Cowork's `bin/` handling appears to be the actual
bug and is worth reporting upstream.

## [1.28.1] - 2026-07-30

Gives up this repo's claim to the `sito-plugins` marketplace name, in favor of a new
dedicated `itoh-shun/sito-plugins` repo that hosts nothing but a marketplace manifest.

- **Why:** two repos (`rig` and `claude-context-checker`) had independently renamed
  their own marketplace to `sito-plugins` on the same day. Claude Code keys
  `known_marketplaces.json` by that name, so whichever was added last silently
  overwrote the other's registration on the CLI. Splitting the shared name out to its
  own repo removes the collision regardless of which repo a user adds first.
- **Correction (see 1.28.2 below):** this entry originally claimed the trigger was
  Cowork excluding a plugin whose source resolves to the same repo as the marketplace
  listing it. That was disproven by later, controlled testing — see 1.28.2 for the
  actual cause. The name-collision fix above is real and stands on its own; the Cowork
  symptom that prompted it turned out to be something else entirely.
- `.claude-plugin/marketplace.json`: `name` is now `rig` (owner stays `sito-plugins`),
  and the `claude-context-checker` entry moved out — it lives in the new shared repo
  instead.
- Install line becomes `/plugin install rig@sito-plugins` via `itoh-shun/sito-plugins`
  (recommended, shared with claude-context-checker) or `/plugin install rig@rig` via
  this repo directly (both READMEs updated, with a migration note).
- `find_rig_home` now tries `rig-sito-plugins`, then `rig-rig`, then
  `rig-itoshun-local-plugins` — every name this plugin's data directory has ever been
  derived from, so no existing install's state is orphaned by this or the 1.28.0 rename.
- `tests/test_plugin_branding.py` reworked around two distinct constants
  (`SHARED_MARKETPLACE` vs `OWN_MARKETPLACE`) instead of one, since they no longer name
  the same thing.

## [1.28.0] - 2026-07-30

Renames the marketplace brand **itoshun-local-plugins -> sito-plugins**. The plugin
itself stays `rig`, so every `/rig:*` command id is unchanged.

- `.claude-plugin/marketplace.json`: `name` and `owner.name` are now `sito-plugins`.
- `.claude-plugin/plugin.json` `author.name`, `action.yml` `author`, and
  `pyproject.toml` `authors` carry the brand.
- Install line becomes `/plugin install rig@sito-plugins` (both READMEs, plus a
  migration note for anyone who added the marketplace under the old name).
- **The data directory follows the marketplace name**, so this is not cosmetic: Claude
  Code derives it as `<plugin>-<marketplace>`. `find_rig_home` now tries
  `rig-sito-plugins` first and falls back to `rig-itoshun-local-plugins`, so an install
  made before the rename keeps resolving instead of having its state orphaned.
- New `tests/test_plugin_branding.py` pins the brand across marketplace/plugin/action/
  pyproject/README, asserts the plugin name is *not* rebranded (it drives the command
  ids), and covers the legacy data-directory fallback.

Historical `itoshun` paths in CHANGELOG entries and `docs/superpowers/plans/` are left
as-is — they record what was run at the time.

**Also ships 1.27.1** (below), which landed in the same merge and therefore has no
release of its own: the fix for the lost-update race in the local queue backend, where
`queue go` reported items done while `.rig/queue.json` still had them at running/queued,
and concurrent writers could drop the whole backlog.

## [1.27.1] - 2026-07-30

Fixes a lost-update race in the **local queue backend**. `queue_set_status` and
`queue_add` did an unlocked load -> modify -> save on `.rig/queue.json`, while
`queue go` mutates that store from `--max-parallel` threads — and the default is
3, so this was on by default. Concurrent writers clobbered each other:

- `queue go` reported `16/16 done` while `queue list` still showed 4 items as
  `running`/`queued` (reproduced end-to-end; 1 in 3 runs at 16-way parallelism).
- Transitioning 20 items concurrently lost 16 of the 20 updates.
- 30 concurrent `queue add` calls left **1** item: a `_local_load` that swallowed
  every exception returned an empty queue on a torn read, and the next save
  persisted that — destroying the backlog with no error.
- An item whose `done` write was clobbered stayed `running` forever; one that
  fell back to `queued` was **re-executed by the next `queue go`**.

`_run_one` also discarded `queue_set_status`'s return value, so none of this was
visible, and an exception inside it propagated out of `ex.map`, discarding the
other results and pinning every remaining item at `running`.

- Serializes the whole read-modify-write behind `threading.Lock` (for `queue go`'s
  thread pool) **plus** a blocking `fcntl.flock` (for separate processes — a
  `queue add` in another terminal, or the rig/claude providers' subprocesses).
  Same defect class already fixed for the trust store in `recipes.py`.
- Writes the store atomically (tmp + `os.replace`), so a reader can never observe
  a half-written file.
- An unreadable `queue.json` now raises `QueueCorrupt` and stops with an explicit
  message instead of degrading to an empty queue. A store we cannot read is never
  a reason to reset it.
- `_local_load` normalizes a hand-edited store: a missing `items` becomes `[]`, and
  a missing or stale `next_id` is recomputed as max(id)+1 instead of raising
  KeyError or handing out a duplicate id.
- A status update that does not land prints `[WARN] #<id>: could not record status
  ...` with the reconciling command, and one failing item is marked `failed`
  rather than taking the rest of the batch down.

`github`/`gitlab` backends keep their state in issue labels and were unaffected.
Regression tests cover concurrent add/status in-process, concurrent add across
processes (the flock layer), the corrupt-store refusal, and store normalization.

## [1.27.0] - 2026-07-29

Splits SKILL.md into a lean core plus on-demand reference files — the follow-up
named in 1.26.1. The engine body was **881 lines / 151 KB (~58k tokens)**, which
is loaded in full every time the skill triggers; that is roughly a third of a
200k context spent before any work starts, and it contradicts the engine's own
§6 "context-minimal が絶対条件" rule. Now **670 lines / 85 KB (-42%)**, with no
rule removed — every detail moved to a file that is read when it is actually
needed (the same pattern 減量フェーズ1/2 used for `--list` / `--plan`).

- **`facets/instructions/resolve`** (new) — the canonical §4 detail: manifest key
  meanings and defaults, recipe tier search reporting, `extends` N-level merge
  and `remove: true` error handling, the flag⇔recipe-key equivalence table,
  slice error formats, and `--save-recipe` save rules / snapshot semantics.
  §4 keeps the resolution order, the tier table, size-aware and autonomy.
- **`facets/instructions/run-report`** (new) — the canonical flow-completion
  report and `.rig/runs.jsonl` telemetry spec (field definitions, mode
  modifiers, slice/`--skip` variants, `failure_mode` typing). §6 keeps the
  discipline and the header skeleton.
- **`PACKS.md`** (new) — the long-form pack descriptions from §2. The §2 table
  keeps **every brick name plus a one-line summary**, so `--validate`'s catalog
  drift check (backticked brick refs → real files) still covers the inventory.
- **De-duplicated the flag⇔recipe-key boilerplate.** 8 near-identical
  "`X: true` キーの解釈" blockquotes and 13 near-identical "保存する X 値"
  bullets collapsed into one general rule (equivalent / saved / visualized) plus
  a single 14-row table. §3.5's per-key rows collapsed the same way.
- §2 now lists `facets/instructions/validate` (previously only referenced from
  §3), and the movie pack's `render-{remotion,davinci,aviutl}` stubs are listed
  in brace form so catalog drift keeps seeing them.

No behavior change: `--validate` is unchanged at 52 PASS / 5 WARN / 0 FAIL, and
the §4.1–§4.5 section anchors other bricks cite are preserved.

## [1.26.1] - 2026-07-24

Fixes a name collision that made the engine skill fail to register. The engine
lived at `skills/rig/SKILL.md` with `name: rig`, which a plugin resolves to
`/rig:rig` — the exact command id already claimed by `commands/rig.md` (the
`/rig:go` compatibility alias). Two things fighting over `rig:rig` meant the
engine skill didn't appear in the skill listing, so `/rig:*` commands that load
it via the Skill tool fell back to a non-gated "normal" run (no isolated
worktree, no acceptance gate).

- Renames the engine skill to **`name: engine`** → `/rig:engine` (directory
  stays `skills/rig/` so all `facets/ patterns/ recipes/` brick paths still
  resolve). The `/rig:rig` alias command is preserved.
- Standardizes every command's "load the … skill via the Skill tool" instruction
  to `rig:engine` (34 command files; previously 30 said `rig`, 4 said `rig:rig`).

If the engine skill still doesn't register after this, the remaining suspect is
the SKILL.md's size (881 lines / ~151 KB, far beyond a skill body's intended
footprint); splitting it into a lean core plus on-demand reference files is the
follow-up.

## [1.26.0] - 2026-07-24

Removes the "run the tool, then hand its output in" two-step from the sensor
flow. `scripts/sast_adapter.py` gains an opt-in one-step `run` subcommand:

```
python3 scripts/sast_adapter.py run semgrep --path . --apply <task_id>
python3 scripts/sast_adapter.py run pip-audit --apply <task_id>
python3 scripts/sast_adapter.py run claude-security --apply <task_id>
```

`run <tool>` invokes a standard **local static** scanner
(semgrep / pip-audit / npm-audit / trivy) on your own code with its default
JSON-producing flags (append tool-specific flags after `--`), parses the output,
and records the gate criterion — no temp file, no second command. It stays
static + local, so the ethical boundary is unchanged; when the tool isn't on
`PATH` it exits with guidance to the pipe-in form. `run claude-security` is
special-cased: the plugin is a Claude Code command, not a CLI, so `run`
**auto-discovers the newest `CLAUDE-SECURITY-<ts>/CLAUDE-SECURITY-RESULTS.jsonl`**
in the repo and applies it — you no longer hunt for the timestamped path.

The pipe-in form (rig never runs the tool; e.g. CI runs the scan separately) is
unchanged and still the default contract (#276). `/rig:sec` and
`security-monitor` now lead with the one-step form. Verified by
`tests/test_sast_adapter.py`.

## [1.25.0] - 2026-07-24

Closes the diff-scope blind spot the model-invariance panel found — with a
whole-repo detector folded into the gate, not a deeper LLM lens.

The panel established that diff-scoped review structurally cannot catch a flaw in
trusted, *unchanged* code (the `is_owner` helper never appears in the diff). The
fix is a detector that scans the *whole tree*. `scripts/sast_adapter.py` now
ingests two more formats and routes them to the acceptance gate:

- **`claude-security`** → `deep_scan_findings_clear`: the official Claude
  Security plugin's `CLAUDE-SECURITY-RESULTS.jsonl` (one finding per line;
  multi-agent, cross-file, targets auth bypass). Key names are read as tolerant
  aliases since the plugin's schema is not published as fixed.
- **`sarif`** → `sast_findings_clear`: SARIF 2.1.0, the interoperable format
  from CodeQL, `semgrep --sarif`, and the managed Claude Security export.

rig still never runs the tool — you run the whole-repo scan and hand its output
in (#276), and register the optional criterion in `.rig/gates.json`. Because
those scanners read the entire tree, a finding in unchanged code the change
trusts now blocks `accept` through the same gate the diff-scoped reviewer
couldn't. Demonstrated end to end: a `CLAUDE-SECURITY-RESULTS.jsonl` flagging the
`authz.py` null-owner bypass yields `deep_scan_findings_clear=failed` — the exact
defect the reviewer had approved. AI judgment for the whole-tree threat model,
rig's deterministic gate to enforce it. `security-monitor` and `/rig:sec` wire it
into the scan loop. Verified by `tests/test_sast_adapter.py`.

## [1.24.1] - 2026-07-23

Fixes the *upstream* half of the gate blind spot 1.24.0 addressed. Adding the
detection lens to `security-reviewer` (1.24.0) does nothing if the reviewer is
never dispatched — and investigating the panel failure showed exactly that: the
adaptive risk router (`analyze_diff`) keyed on `authorization`/`ownership`/
`current_user`, so an authorization fix written as `if not is_owner(user, doc):
raise Forbidden(...)` produced **zero security signals** and routed to
`test-reviewer`. security-reviewer never ran on the `trusted-helper-authz` task,
which is why rig shipped its silent defect. Gate detection needs both routing
*and* the lens; 1.24.0 shipped the lens, this ships the routing.

- **`analyze_diff` security patterns** now also match authorization helpers and
  ownership (`is_owner`, `can_access`, `has_permission`, `permission`,
  `forbidden`, `is_admin`, `owner`, `role`, `acl`, `access-control`,
  `unauthorized`), multi-tenant isolation (`tenant`, `tenant_id`,
  `multi-tenant`), and input validation/sanitization (`validate`, `sanitize`,
  `allowlist`/`denylist`/`whitelist`/`blacklist`). An ownership / tenant /
  validation fix now routes to `security-reviewer` even when it never says the
  word "authorization". Verified by `tests/test_adaptive_risk.py`.

## [1.24.0] - 2026-07-23

Strengthens the gate where the model-invariance panel proved it was blind — the
real lever for a stronger, more model-invariant rig.

The first real panel on `benchmarks/hard-tasks` (Haiku + Sonnet + Fable, N=3,
convergence budget on) established two things: (1) the traps are real and
model-independent — all three models, including the strongest, shipped the
`trusted-helper-authz` silent defect on every bare run (9/9); (2) rig barely
helped there (safe_rate tied bare at 50%), because the `None owner == None id`
bypass fools rig's `security-reviewer` too — the gate *passed* the defective
attempt, and a retry budget only fires when the gate *fails* one. Where the
generator and verifier share a blind spot, rig ships the defect. That is rig's
own thesis, measured: safety is bounded by the gate's detection ability, so the
lever is stronger gates, not more iteration.

### Added / Changed

- **`/rig:drill` seed catalog → `corpus_version: 3`** (27 → 29 classes): adds the
  two blind-spot security classes the panel exposed — `認可ヘルパー誤信`
  (trusting a flawed auth helper; null-match bypass, CWE-863) and
  `多サイト検証漏れ` (validation added at one call site, another sink left
  unguarded, CWE-20) — so the reviewer's detection rate on them is now
  measurable rather than assumed.
- **`security-reviewer` detection lenses** (facet persona + `agents/` mirror +
  `appsec-checklist` knowledge): don't trust an existing auth helper — check it
  for null-match bypass / type confusion / default-allow; and verify at the
  shared sink, not one call site (catch the "fixed one entry point" miss).
- **`benchmarks/hard-tasks/README.md`** records the panel results and the
  bounded-by-the-gate finding in full (measured, not asserted).

Whether the new lenses actually raise the reviewer's detection rate — and move
rig's authz safe_rate off 0% — is the next measurement (`/rig:drill` scores it;
a panel re-run would confirm it end to end). This ships the detector and the
yardstick; it does not yet claim the number moved.

## [1.23.1] - 2026-07-23

Fixes the model-invariance metric after the first real panel run on the hard
corpus exposed a flaw in it — exactly the measure-then-correct loop rig is built
on.

The run (Haiku + Fable, `benchmarks/hard-tasks`) showed the hard tasks work:
both models, run bare, shipped a silent security defect on the
`trusted-helper-authz` trap (even the stronger model trusted the flawed helper).
rig halved the silent-defect rate (bare 50% → rig 25%). But the old headline
read "rig agreement 50% vs bare 100% (rig −50%)", making rig look *worse* —
because exact-outcome agreement counted a `clean_pass`-vs-`safe_stop` split as
disagreement, when both outcomes mean "did not ship a defect".

- **New `safe_rate`** per arm (`clean_pass` + `safe_stop` over valid samples) —
  the floor-invariance number: "regardless of the model, did rig avoid shipping
  a defect". Reported for both arms; on the pilot it reads rig 75% vs bare 50%.
- **Verdict now gates on safety, not exact agreement.** `unsafe` when rig's
  panel silent-defect rate > 0 (unchanged); a new **`safe_but_split`** covers
  "safe on every model, but outcomes split clean/safe-stop" (floor-invariant,
  capability still varies) so a safe split is no longer mislabeled
  `model_sensitive`; `model_sensitive` now means rig actually shipped a
  broken/wrong result on some model.
- HTML report leads with rig/bare `safe_rate` and silent-defect, with
  outcome-agreement demoted to a secondary signal. Verified by
  `tests/test_bench_invariance.py`.

## [1.23.0] - 2026-07-23

Adds a **model-invariance metric** — the first step toward rig's "strongest =
results not swayed by the model" goal, and true to rig's own rule that gate
efficacy is *measured, not asserted*.

The claim behind rig is that the accepted result's quality is bounded by the
**gate**, not by the **model**. `rig-wb bench-invariance` turns that into a
number: it runs the existing paired benchmark once per model in a panel (both
arms driven by that model, reusing `bench.run_benchmark` unchanged), then
measures — per arm — how much the *terminal outcome* varies across the panel.

- **agreement**: fraction of (model × run) samples that reached the same
  outcome for a task; 1.0 = the outcome does not depend on the model.
- **panel silent-defect rate**: did any model, on any run, ship a
  passes-public-but-fails-hidden result. Must be 0 for a model-invariant *and
  safe* harness — a nonzero rig rate forces an `unsafe` verdict regardless of
  agreement.

The headline `model_invariance_score` is the rig arm's mean agreement, reported
next to the bare arm's so the honest comparison (does rig converge outcomes the
bare model splits?) is visible. Infra/invalid samples are excluded from
agreement so CI flakiness cannot masquerade as model sensitivity.

Note on interpretation: on an easy corpus where every model already succeeds
bare, both arms score ~1.0 (trivially invariant) — the metric only *discriminates*
on tasks where bare outcomes diverge by model. Building that harder corpus is
tracked as follow-up; this ships the instrument, verified by unit tests on the
pure scorer (`tests/test_bench_invariance.py`).

This release also adds the first *mechanism* for raising invariance, not just
measuring it: an opt-in **convergence budget**. rig already feeds a failed
step's distilled findings back into the next attempt (#333 `previous_failure`);
`RIG_CONVERGENCE_K=<n>` raises the per-step retry cap so a run keeps iterating on
that feedback for more attempts before escalating. A weaker model thus gets more
feedback-guided chances to converge on a gate-passing result instead of stopping
— extending the range of models whose *accepted* outcome matches a stronger
model's. It only ever raises a step's K (never lowers an explicit recipe
`max_retries`) and is a complete no-op when unset, so all existing behavior is
unchanged unless a run opts in.

### Added

- `rig-wb bench-invariance --provider <p> --models m1,m2,m3 [--corpus ...]
  [--runs N] [--agreement-threshold 0.8] [--out ...] [--html ...]`: model-panel
  invariance report (JSON + HTML). Paid providers require the same
  `--allow-paid-provider` opt-in as `rig-wb bench`.
- `rig_workbench/bench_invariance.py`: pure scorer (`score_invariance`,
  `classify_arm_dict`) plus the panel runner and HTML renderer.
- `RIG_CONVERGENCE_K` convergence budget: `config.effective_k()` raises a step's
  retry cap to the budget when set (`> 0`), plumbed through `recipes.load_steps`.
  Verified by `tests/test_convergence_budget.py`.

## [1.22.0] - 2026-07-23

Adds a **security (white-hat) pack**: an attacker-perspective layer that
proactively hunts vulnerabilities in existing code, proves them with a PoC,
drives a gated fix verified to actually close the hole, and can run on a
scan-only monitoring loop — plus a dedicated benchmark corpus that quantifies
the with-vs-without-rig difference on security work.

The value framing is the same as the rest of rig, applied to security: a bare
agent asked to "fix this vulnerability" tends to write a plausible band-aid
(a denylist, a specific-payload block) that passes the visible tests while the
hole stays open — a *silent security defect*. The pack refuses to ship that.
Findings must separate Confirmed (a PoC actually landed) from Suspected
(insufficient information), fixes must turn the PoC into a red-then-green
regression test, and `accept` stays blocked until the re-exploit fails.

Ethical boundary is built in and non-negotiable: scope is the user's own
product or an explicitly authorized local/staging environment, and the pack is
**static analysis + local verification only** — it never sends attack traffic
to a live service. Dynamic scanning (DAST) is deliberately out of scope and
left behind an authorized-target allowlist.

### Added

- **`/rig:sec` command** with three sub-modes: `audit` (recipe
  `security-audit`, read-only attacker-perspective sweep of existing code),
  `fix` (recipe `pentest-fix`, PoC→regression-test→canonical-fix→re-exploit,
  gated by `exploit_reproduced_then_closed`), and `monitor` (recipe
  `security-monitor`, periodic SAST/SCA/secret re-scan on
  `patterns/autonomous-loop`, scan-only).
- **Personas** `security/exploit-researcher`, `security/threat-modeler`,
  `security/remediation-engineer` (reusing the existing `security-reviewer` as
  the independent verifier), knowledge wiki `attack-catalog` (extends
  `appsec-checklist` with exploitation technique lenses and the ethical
  boundary), and output-contract `security-findings` (Confirmed/Suspected
  split; attack scenario, PoC, `file:line`, root cause, and a *canonical* fix
  are all mandatory; low-confidence Critical/High is forbidden).
- **SCA support in `scripts/sast_adapter.py`**: `pip-audit`, `npm audit`, and
  `trivy fs` JSON now fold into a `sca_findings_clear` acceptance-gate
  criterion, alongside the existing semgrep → `sast_findings_clear`. rig still
  never runs the tool — you pipe its output in (#276 design).
- **Security benchmark corpus** `benchmarks/security-tasks/` (6 stdlib-only
  Python tasks: absolute-path traversal, shell command injection, SSRF via
  private/metadata IPs, unsalted password hashing, seedable-PRNG reset tokens,
  IDOR). Each ships a `narrow` variant (the plausible fix that passes the
  public suite but fails the hidden exploit) and a `canonical` variant (the
  real fix). Runs on the existing paired runner —
  `rig-wb bench --corpus benchmarks/security-tasks` — and reports the bare-vs-rig
  silent-defect rate and relative reduction. Guarded by
  `tests/test_security_bench_tasks.py`.
## [1.21.3] - 2026-07-23

Closes #341: `scripts/validate.py` (CI) previously had no manifest checks at
all, despite `facets/instructions/validate.md` §2 specifying them in detail —
a malformed `.claude/rig.md` (e.g. `default_backend: "manul"`, a typo) was
silently swallowed at RESOLVE/COMPOSE time and never caught by CI, only by a
human remembering to run `/rig:dev --validate` by hand.

### Added

- `rig_workbench/validation/manifest.py` (`check_manifest()`): CI-checks the
  5 manifest value keys that are mechanically type/enum/ordering-determinable
  — `default_backend` (`manual`/`workflow`), `default_budget` (`low`/`mid`),
  `default_orchestrate` (boolean), `worktree.enabled` (boolean), and
  `size_thresholds` (positive-integer subkeys, ascending
  `S_max < M_max < L_max` with generic defaults substituted for unset
  subkeys). Silently skips when `.claude/rig.md` doesn't exist (manifest is
  optional) or has none of these 5 keys set. Wired into `validate.py`'s
  `main()` alongside the other checks.
- 11 synthetic positive/negative fixtures in `scripts/validate.py selftest`
  covering all 5 checks, plus a standalone `tests/test_manifest_check.py`
  (24 tests) exercising `check_manifest()` directly.
- `default_recipe`/`default_personas[]` (tier reference resolution) and
  `knowledge.*` (path existence) remain unimplemented in CI — they need the
  same project→user→shipped resolver COMPOSE uses, a different scope from
  this issue's 5 self-contained value checks. `validate.md` §2 now notes the
  CI-implemented/unimplemented split explicitly.

## [1.21.2] - 2026-07-23

Documentation-only sync closing two discoverability gaps (#337, #327).

### Fixed

- README.md / README.ja.md's Positioning section still called the MCP server
  "proposed but not shipped," contradicting §7's own shipped documentation of
  `scripts/mcp_server.py` (#263). Updated to point at §7 instead (#337).
- `stream-checks` / `stale-refs` / `scan-destructive` / `instincts` (all
  implemented and tested `workbench.py` subcommands) were missing from every
  summary listing: `workbench-ops.md`'s opening line, `SKILL.md` §2's
  workbench pack row, `SKILL.md` §10's reference table, and `commands/go.md`
  (frontmatter `description`/`argument-hint` and the ① subcommand table).
  Added to all of them (#327).

## [1.21.1] - 2026-07-23

A real `--bare-model fable --rig-model sonnet` cross-model comparison (all 10
tasks x 3 runs, `--allow-paid-provider`) passed the recipe's own acceptance
gate: rig(sonnet)'s silent-defect rate is 3.3% vs bare(fable)'s 10.0% (66.7%
relative reduction), rig safe-stop 10.0%, call ratio 2.37x. Honest caveat: most
of that delta traces to one task (`ts-api-compat-export`) where fable-bare
produced a silent defect in all 3 runs while rig(sonnet) recovered to
clean_pass in 2 of 3 via its review/repair loop — this is evidence that rig's
review path caught one systematic model blind spot, not a general "cheap
model + rig beats a stronger model" result.

Investigating the run's 3 safe-stops (with a second opinion from Codex
gpt-5.6-sol, read-only) surfaced and fixed a real bug (#342):
`_execute_targeted_review()` only ever inspected `verdicts[0]` (the primary
reviewer), so when a second high-risk domain adds a secondary reviewer and
the primary passes, a repairable FAIL from the *secondary* reviewer was never
attempted — it escalated unconditionally instead. Fixed to attempt repair on
the single failing verdict regardless of primary/secondary position.

### Fixed

- `_execute_targeted_review` (`rig_workbench/orchestrate/providers.py`) now
  finds all failing verdicts and attempts `execute_informed_repair` when
  exactly one verdict failed, instead of only ever checking `verdicts[0]`.
  All existing safety conditions (allowlist match, diff-changed, generator
  success, post-repair check, invocation budget) are unchanged (#342).

## [1.21.0] - 2026-07-23

Adds `--bare-model`/`--rig-model` to `rig-wb bench`, a per-arm model override
for a third benchmark question the same-model pairing (1.20.0's Claim B)
can't answer: can a cheaper model driven by rig approach a stronger model's
bare output? Both flags default to `--model` when omitted, so the historical
same-model-both-arms behavior and its `score_provider` single-identity
invariant are unchanged unless a run explicitly opts in. The JSON report now
carries `bare_model`/`rig_model` alongside the existing `model` field (which
stays the rig arm's model for backward compatibility), and the HTML report's
provider/model card shows both when they differ.

### Added

- `rig-wb bench --bare-model <model> --rig-model <model>`: override the model
  for a single arm. `run_benchmark`/`run_pair` resolve each arm's model
  independently only when at least one override is given (otherwise a single
  resolution is reused, preserving the prior local-provider discovery
  behavior and call count).

## [1.20.1] - 2026-07-23

Closes the TypeScript follow-up left open by 1.20.0 (#338): the backtick/quote
`MECHANICAL_CHECK` unwrap fix was re-verified with a real
`--allow-paid-provider` Codex (gpt-5.5) run across all 5 TypeScript benchmark
tasks (`ts-api-compat-export`, `ts-async-error-propagation`,
`ts-auth-sibling-handler`, `ts-generated-file-modification`,
`ts-stale-cache-mutation`), 3 runs each. Result: 15/15 valid pairs, 0%
rig safe-stop, 0% rig silent defects (one transient `provider_failure` infra
error was retried to reach 3 valid runs on `ts-auth-sibling-handler`). The
Codex safe-stop regression from 1.20.0 is now confirmed resolved on both the
Python and TypeScript halves of the corpus.

### Fixed

- No code change in this release — this is a re-verification-only entry
  confirming the 1.20.0 `execute_informed_repair` backtick/quote unwrap fix
  also holds on the TypeScript half of the benchmark corpus (previously
  untested because the Codex account hit its usage limit mid-verification).

## [1.20.0] - 2026-07-21

A real paired bare-vs-rig benchmark run (10 tasks x 3 runs x 2 providers,
`--allow-paid-provider`) on `adaptive-bugfix` surfaced and drove the fix of a
real bug (see Fixed): `cfg["cwd"]` was never set outside `--isolate`, so risk
assessment always saw an empty diff (permanent fallback to `test-reviewer`,
security/design routing never fired) and informed-repair's diff-changed check
always compared `""` to `""`. Post-fix, Claude passes the recipe's own
acceptance gate outright: 0% rig silent defects vs 3.3% bare (100% relative
reduction), 0% safe-stop (was 60% pre-fix), 2.33x call ratio. Codex's
wrong-default-value silent-defect regression is fully resolved (0% both arms,
was 6.9% rig vs 0% bare pre-fix). Codex's safe-stop rate had also risen to
27.6% (over the 20% threshold) now that risk assessment sees real diffs;
tracing showed gpt-5.5, unlike sonnet, reliably wraps an otherwise
well-formed, repair-eligible `MECHANICAL_CHECK` value in backticks, which
broke the exact byte-for-byte allowlist match and made repair permanently
unrepairable. Stripping one symmetric layer of backtick/quote wrapping before
the allowlist comparison (never enough to make an unrelated string match —
the stripped result still has to equal an allowlisted command exactly)
eliminated safe-stop on every Python task re-verified with a real
`--allow-paid-provider` Codex run (0/3, 0/3, 0/3 across the three previously
worst-affected tasks). Full re-verification of the TypeScript half is
follow-up work: the Codex account hit its usage limit mid-verification.

### Added

- Added the opt-in `adaptive-bugfix` recipe. Its normal path uses two model
  calls (implementation and one targeted review); deterministic diff-risk
  assessment selects the reviewer, with a second review or one bounded repair
  call only when risk or failed checks justify it. Existing default recipe
  routing is unchanged.
- Rebuilt `rig-wb bench` around 10 repository-shaped Python and TypeScript
  tasks, paired writable bare/rig workspaces, externally isolated hidden
  checks, exact provider-call journals, and provider/model-scoped scoring.
  Acceptance requires at least 3 valid pairs for each of at least 10 tasks,
  at least 50% fewer rig silent defects, no more than 20% rig safe stops,
  average rig calls no more than 2.5x bare, and no more than 10% infrastructure
  errors. A zero bare silent-defect count is inconclusive, not a pass.
- Benchmark JSON is now schema version 2 and records corpus, provider, concrete
  model, validity, outcomes, calls, infrastructure errors, unrelated diffs,
  and workspace leaks. The HTML renderer retains compatibility with schema-v1
  reports. Mock results are labeled `WIRING ONLY`.
- Real Claude/Codex benchmark execution now requires the explicit
  `--allow-paid-provider` opt-in. Benchmark CLI exit codes are `0` for a
  passing result, `1` for completed fail/invalid/inconclusive results, and `2`
  for CLI or schema errors.

### Fixed

- `adaptive-bugfix`'s risk assessment and informed-repair diff detection
  (`_git_diff_evidence` / `_git_changed_files` in
  `rig_workbench/orchestrate/providers.py`) silently analyzed an empty diff
  on every real (non-`--isolate`) headless run, because `cfg["cwd"]` is only
  ever set inside the `--isolate` branch of `cmd_run`. This both defeated
  security/design risk routing (permanent fallback to `test-reviewer`) and
  made `execute_informed_repair`'s diff-changed check always `False`
  regardless of what the repair generator actually wrote to disk. Both now
  fall back to `config.INVOCATION_CWD`, matching the fallback the mechanical
  check subprocess already used. The same gap independently no-opped the
  local-provider (ollama/lmstudio) generator dispatch; claude/codex were
  accidentally unaffected there since their subprocess `cwd=None` inherits
  the parent process's cwd.
- The implement step's blanket "do not change tests" rule made any reviewer
  FAIL that asked for missing test coverage permanently unrepairable (no
  mechanical check can ever be "add a test"). It now permits adding exactly
  one narrowly-scoped verification test: on first pass when the fix's
  correctness depends on an unstated default/edge-case value, or during the
  one-shot informed-repair pass when the reviewer named the input/behavior
  via an allowlisted mechanical check.
- `execute_informed_repair`'s `MECHANICAL_CHECK` allowlist match failed
  whenever a reviewer wrapped an otherwise-correct command in backticks or
  quotes (`` `/usr/bin/python3 -m pytest -q` ``) — a formatting habit gpt-5.5
  reliably exhibits in this contract but sonnet does not, which is why the
  same recipe/prompt safe-stopped far more on Codex. `_unwrap_inline_markup`
  strips one symmetric layer of such wrapping before the comparison; the
  result must still match an allowlisted command byte-for-byte, so this can
  only fix a false-negative match, never let an unrelated string through.

rig の変更履歴。バージョンは `.claude-plugin/plugin.json` に対応。
形式は [Keep a Changelog](https://keepachangelog.com/) に準拠（日付は JST）。

> リリースタグは GitHub 側で発行する（実行環境の都合でタグ push を別途行う運用）。

## [1.19.0] - 2026-07-19

Measurement-driven release: two benchmarks were built to answer "is rig
worth using?", and the live (real-provider) runs they enabled uncovered
four real defects in the headless pipeline — each filed, fixed, and
re-measured in this release. Final measured state on the adversarial
bench task (9 rig runs vs 9 bare runs): silent defects bare 1/9,
rig 0/9; unnecessary escalations on correct code eliminated (2→0);
correct code now passes the 3-way review unanimously on the first vote
while genuinely defective fixes still get stopped.

### Added

- **Two benchmarks answering "is rig worth using?" (#330)**: the claim
  splits into two, and only one is provable without spending money.
  New `rig-wb sensor-bench` (zero LLM calls, zero billing, fully
  deterministic) runs the secrets/injection/destructive machine
  sensors against a fixed corpus of known-bad lines and safe
  near-misses — current result: 10/10 known-bad lines caught, 0/7
  false positives. The point isn't the number itself, it's that a
  bare LLM loop has **no number here at all** — nothing runs these
  checks unless something wires them in, so its guaranteed catch rate
  on this corpus is 0% by construction. This is a floor, not a
  ceiling: it says nothing about judgment-requiring defects (that's
  `/rig:drill`'s and `rig-wb bench`'s territory). Separately, `rig-wb
  bench` (bare-vs-rig with a hidden spec-check per task, shipped
  since v1.9.0) turned out to have **shipped with zero tests and zero
  README mention** — it's now documented in both READMEs and covered
  by a mock-mode smoke-test suite. Honest scope, stated explicitly in
  code and docs: `--provider mock` only proves the harness plumbing
  works (MOCK_SRC hardcodes the built-in tasks' fixes) — it is *not*
  evidence for the bare-vs-rig quality claim; only a real-provider run
  is, and that costs real money, so this repo doesn't run or publish
  it automatically.

  A real (`--provider claude`) run of the original 4 tasks under
  `fast-bugfix` also surfaced #331 (see Fixed below) and, once fixed,
  showed bare and rig converging on identical spec-check-passing
  output — no quality delta on that task set, only cost (rig ~20-40x
  slower, 3x more calls for the same result). That's an honest, small
  result, not a generalizable one: those 4 tasks are single-file and
  self-evidently specified, and `fast-bugfix` deliberately skips
  review-diff — rig's most likely source of differentiation on harder
  tasks was never exercised. `rig-wb bench` gained a 5th task,
  `auth-bypass-sibling`, built to test exactly that gap: the bug
  report names one method (`get_profile`) with a missing
  ownership check; a sibling method (`update_profile`) has the
  identical bug but is never mentioned in the goal or the
  deliberately-weak visible tests. A narrow fix of only what was
  asked passes the visible tests and fails the hidden spec — locked
  in by a regression test so the corpus can't silently drift.

  bench results now carry an asymmetric outcome classification
  (`classify_outcome`), because "failed" means opposite things in the
  two arms: `silent_defect` (claimed done, hidden spec broken — the
  worst outcome, nothing signals a human to look) vs `safe_stop`
  (rig-only: escalated to a human although the code was actually
  right — over-conservative but honest) vs `stopped_wrong` /
  `clean_pass`. The live hard-task run produced exactly this split
  (bare: 1 silent defect in 3 runs; rig: 0 silent defects, 2 safe
  stops), so the report format now names it instead of burying it in
  exit codes. Surfaced per-run in stdout, as HTML KPI tiles, and as
  per-task outcome columns; old JSON without the field still renders.

- **grok-build host adapter + `--provider grok` (#328)**: grok-build
  (xAI's terminal coding agent) documents full Claude Code
  compatibility — plugins/skills/hooks/MCP/CLAUDE.md auto-load with
  zero configuration — so `scripts/host_adapters.py` gains a
  `grok-build` entry as a **native passthrough**: canonical hook-event
  names pass through unchanged and `hooks/hooks.json` is reused
  verbatim (no host-specific copy). Every capability is `unverified`
  (the compat claim is theirs; no grok CLI exists in this environment
  to exercise it live). `orchestrate` gains a `grok` provider
  (`grok -p <prompt> --output-format plain`, per-step `-m` model
  support) with one gap declared honestly: grok headless documents no
  read-only/sandbox flag, so the verifier role's read-only stance
  rests on the prompt contract alone — one enforcement layer thinner
  than `claude` (`--allowedTools`) or `codex` (`--sandbox read-only`).
  `--always-approve` is never passed (auto-approves tool executions;
  a generator that wants it opts in via `--provider-cmd`). Covered by
  host-adapter golden-fixture tests and selftest argv probes; existing
  providers untouched.

- **Size-based auto-tiering at routing (#324)**: after task-type
  classification, the workbench routing estimates a size tier from the
  input — S (single file / few lines / self-evident fix) steers bugfix
  to `recipes/fast-bugfix` (no reviewer fan-out, minimal gate), M
  stays on the standard recipes, L turns the design/review steps on.
  What gets lighter is step and verifier count, never the safety
  machinery: the isolated worktree and acceptance-gate hold on every
  tier ("small" is not an exemption from isolation). The tier and its
  reason surface in the routing banner, explicit `--recipe`/`--only`
  always beat the auto-tier, and a mis-judged S escalates via the
  stuck-guard ("2 stalls → propose re-running under the full recipe")
  with the misjudgment logged as calibration material.

### Changed

- **First-run positioning sharpened (#325, from external review)**:
  README §1 now states rig's honest self-definition — it does not
  automatically produce quality; it makes the AI unable to ignore the
  quality bar you define, and it deliberately trades speed and tokens
  for that safety. §2 adds a directly-vs-through-rig comparison table
  (failed attempts / "it's done" / review quality / what happened) and
  states the zero-configuration property explicitly. No new concepts
  or sections — density up, count unchanged.

### Fixed

- **Headless verify verdicts had no blocking/non-blocking distinction —
  advisory findings rounded up to FAIL and deadlocked quorum=all
  (#334, discovered by the post-#332 re-measurement)**: the
  interactive review-verdict contract has always had
  `APPROVE_WITH_CONDITIONS`, but `_build_verify_prompt` forced a
  binary `VERDICT: PASS|FAIL`. Once #332 gave each reviewer its real
  lens, lens-faithful advisory findings ("no regression test in the
  diff" — on a task whose goal *forbade* touching tests) had no
  conditional-approve outlet, so first-round votes went 0/3 on
  objectively correct code and every run escalated. Fix ports the
  conditional-approve semantics to the headless path: the verify
  prompt now instructs FAIL is ONLY for a blocking defect statable as
  a one-line concrete failure/attack scenario, non-blocking findings
  go to reasoning + `VERDICT: PASS_WITH_CONDITIONS`, and
  `_verdict_ok` recognizes the new token explicitly (`_PASS_TOKENS` —
  it previously passed only by accident of prefix matching). Not a
  weakening: blocking defects still FAIL (the live run where
  reviewers caught a genuinely-narrow fix and stopped it —
  `stopped_wrong`, defect NOT shipped — is the behavior being
  preserved). The bench task's goal also stopped forbidding new test
  files ("do not modify the *existing* tests"), removing a
  structurally unsatisfiable reviewer demand.

- **Gate-failure RETRY was blind — reviewer findings were discarded
  before the retry generator ever saw them (#333, discovered by a live
  #330 bench run)**: `compute_next`'s RETRY path reset
  `st["verdicts"]` (the reviewers' evidence-anchored findings) and
  nothing wrote `last_failure` for gate-verdict failures, so the
  retried generator received only `attempt: 2` — no idea what the
  reviewers rejected. rig paid for 3 independent reviews, then threw
  them away and re-rolled the dice; the observed "1/3 PASS → retry →
  1/3 PASS → escalate on objectively-correct code" is exactly what
  blind retries look like. Fix: `_distill_failures` summarizes failed
  checks + dissenting verdicts (bounded: 240 chars/finding, 800
  total) BEFORE the reset; the summary lands in `st["last_failure"]`
  (feeding the pre-existing `previous_failure:` line in the next
  attempt's step contract) and on the FAIL history entry (so
  ESCALATE leaves an audit trail of why). Honest scope: this informs
  the retry, it does not guarantee the retry converges — the effect
  is measured by the #330 bench, not assumed.

- **headless review-diff's 3-way review was 3 identical samples of one
  question, not 3 distinct lenses (#332, discovered by a live #330
  bench run)**: a hard-task bench run showed the 3 reviewers
  (security/design/test) disagreeing (1/3, 2/3 PASS) on code that was
  already objectively correct. Root cause: `run_verifiers_parallel`
  recorded each `persona` for telemetry but never put it in the
  prompt — `build_argv`'s real (`claude`/`codex`/`rig`/`grok`)
  branches ignore the `persona` argument entirely, so every reviewer
  received the exact same generic verify prompt. The interactive
  "manual backend" (`/rig` skill via the Agent tool) was never
  affected — there, each persona file genuinely is a distinct
  subagent's system prompt; only the headless CLI provider path had
  this gap. Fix: `run_verifiers_parallel` now prefixes each verifier's
  prompt with its resolved `facets/personas/<name>.md` brief (e.g.
  security-reviewer's explicit "authorization / IDOR" axis — directly
  relevant to the bug that exposed this) when one resolves; falls back
  to the unchanged generic prompt otherwise (no silent no-op
  injection). `mock`'s existing deterministic persona-based pass/fail
  is untouched (verified by test — it never reads prompt content).

- **`--provider claude`/`rig` generator couldn't actually edit files in
  headless mode (#331, discovered by a live #330 bench run)**:
  `build_argv`'s generator branch for `claude` and `rig` set no
  permission flags. Headless `claude -p` has no one to approve
  Edit/Write tool calls, so an unpermissioned generator asks for
  approval it can never receive and silently writes nothing — this
  environment's first-ever real (non-mock) `claude` provider run hit
  it immediately: every task's `implement` step failed its
  `git diff` check twice and escalated. Confirmed live: the exact
  `claude -p "<edit prompt>"` call left a target file untouched; the
  identical call with `--permission-mode acceptEdits` applied the
  edit. Fix adds `--permission-mode acceptEdits` to the generator
  role only (minimum-privilege — edits allowed, nothing else
  blanket-bypassed; not `--dangerously-skip-permissions`). Verifier
  argv (`--allowedTools Read,Grep,Glob`) is untouched — still
  read-only. `codex`'s generator already had its own write mechanism
  (`--sandbox workspace-write`) and was never affected.

- **Trust-store write race (#329)**: `_record_trust` was an unlocked
  read-modify-write with a non-atomic write; manifest A/B (#317)
  records trust from parallel variant threads, so entries could be
  lost under contention (the intermittent `test_manifest_ab` failure).
  Now serialized behind a module lock and written via atomic
  `os.replace` (readers can never observe a half-written store).
  Honest scope: cross-process simultaneous writers remain
  last-writer-wins over the whole store — out of scope because the
  only concurrent writers in practice are variant threads inside one
  orchestrate process. Regression test hammers the store from 8
  threads × 32 entries.

## [1.18.0] - 2026-07-17

### Added

- **Host-native skill lanes (Claude Code built-ins as measured
  reviewers)**: rig's review fan-out (`parallel-review` ②) gains an
  optional native lane — when the live Claude Code session exposes the
  built-in `/code-review` (and `/security-review` for security_review
  tasks), it joins the fan-out as ONE additional vote, its findings
  translated into the existing `review-verdict`/`review-findings`
  contract and its verdict recorded under the persona name
  `native-code-review`. Two disciplines are structural: the native
  lane is subject to the same measurement as every persona (stats
  rubber-stamp detection; in-session `/rig:drill` can include it in
  the fan-out and measure its detection rate — no unmeasured
  reviewers), and it supplements rather than replaces the persona
  quorum (it runs on the session's own model, so making it the only
  lane would collapse independent verification back into same-model
  self-review). `verify` ②-b similarly delegates to the built-in
  `/verify` skill when present — an extra layer that catches
  green-tests-but-broken-flow changes. Headless runs (orchestrate.py
  providers, CI, MCP) have no built-in skills; the lanes are skipped
  silently and the flow structure is unchanged. SKILL.md §8
  (Native-first) now names host built-ins as part of the inventory to
  check, with both disciplines spelled out. The same rule extends to
  host agent types: read-only codebase exploration dispatches use the
  host's **Explore agent type** when available (structurally
  write-incapable — safe for the investigation stage, faster and
  cheaper than a general subagent; `intake` ①), falling back to a
  normal subagent otherwise.

## [1.17.0] - 2026-07-17

### Changed

- **Response-speed pass (#321)** — behavior-identical, measured:
  one gate evaluation went from 23 git subprocesses / 114ms to
  6 / 76ms (sensors now share one diff fetch via an opt-in
  `shared_diff_cache()` scoped to the evaluation, and the schema
  sensor batch-probes its 12 OpenAPI candidates with a single
  `ls-tree` instead of one `cat-file -e` each); `orchestrate.py`
  startup dropped 170ms → 143ms by returning `urllib.request` to
  function-local imports; the plugin description was rewritten from
  6,283 to 1,454 chars (77% less context loaded per session). All
  497 tests pass unchanged — sensor verdicts and outputs are
  identical.

### Added

- **Streaming gate — mid-implementation lightweight checks (#302)**:
  `workbench.py stream-checks <task_id> [--watch --interval N]` runs
  the fast machine sensors (secret / injection / destructive —
  diff-scoped, no LLM, tens of milliseconds) against the task worktree
  on demand, printing findings as hints. The issue's core requirements
  are enforced by shape, not promise: the command never reads or
  writes acceptance.json and always exits 0, so it structurally cannot
  block the final gate — the same detectors run again at gate time,
  where pass/fail is actually decided; streaming is a preview of that
  verdict. Opt-in (nothing calls it automatically; implement.md
  suggests it at natural checkpoints on L/XL implementations), and
  diff-scoped so cost is bounded by the change. `--watch` re-scans
  only when the diff hash changes.
- **Standard drill corpus + prose/design seed classes (#270, #266)**:
  the seed catalog in `facets/instructions/drill.md` is now formally
  the **standard corpus** (`corpus_version: 2`, 27 seed classes) — the
  same language-agnostic yardstick on any repository. `--corpus
  standard|project|all` selects the seed source (project =
  `.claude/rig/drill-corpus.md`, same table schema); each
  drill-results.jsonl run row carries `corpus`/`corpus_version` so
  standard and project-specific scores never blend (rows without the
  field predate the distinction and count as standard), and
  `aggregate_drill_confidence()` gains a corpus filter. v2 adds 9
  prose/design seed classes — AI-smell markers, UX-heuristic and
  WCAG violations, unsourced hype in posts, engagement-structure
  defects, over-the-line attacks (roast), and sales-flow gaps
  (hearing/proposal/closing) — making de-ai-smell, design,
  design-audit, sns-x-post, scenario, roast, and deal-review
  drillable: coverage went from 9/25 to 16/25 gate-bearing recipes,
  clearing all 7 per-recipe validate WARNs (the remaining WARN —
  gate-bearing recipes with no reviewer personas at all — is
  structural: drill measures reviewers, and stats' rubber-stamp
  detection covers those recipes instead). `validate.py` gains
  `check_corpus_integrity` (version marker present, every row carries
  class/provenance/perspective, severity/blocking in range) so corpus
  rot is machine-caught.
- **Manifest A/B — rule changes measured, not guessed (#317)**:
  `orchestrate.py ab <recipe> --manifest-a <path> --manifest-b <path>`
  runs the same recipe concurrently under two manifests — additive rule
  changes can't be evaluated statically, only by running real tasks
  under both. Each variant's worktree gets its manifest written as
  `.claude/rig.md` (the main working tree is never touched) with its
  content hash trust-recorded (explicit CLI provision = consent, the
  `--allow-project-manifest` consent model). Comparison rows are
  labeled `A(<stem>)`/`B(<stem>)`. Honest scope: the variant manifest
  takes effect for nested provider invocations running inside the
  worktree (cwd-based resolution); the parent orchestrate process's
  own `load_manifest()` still reads the invoking repo's manifest.
  Recipe/provider/model stay identical across variants — the measured
  difference is the rules'. The existing recipe-comparison mode is
  unchanged.
- **`scan-injection --deps` — dependency-tree hidden-instruction scan
  (#320)**: explicit opt-in scan of prose files (`*.md`/`*.rst`/
  `*.txt`, never source) under `node_modules`/`vendor`/`third_party`
  for agent-directed injection markers — countering supply-chain
  attacks that plant hidden instructions in third-party docs. Never
  part of the default surfaces (huge trees; AI-library READMEs
  legitimately contain prompt examples, making phrase findings
  especially false-positive-prone there). Invisible unicode stays
  fail-grade — zero legitimate uses, and exactly the hiding mechanism
  such attacks rely on. Recommended actions (review in context; if
  real, pin/quarantine and report upstream) print with the findings.
- **Harness-context load in `runs --cost` (#319)**: the per-recipe
  rollup now closes with a per-provider summary of prompt weight —
  average prompt tokens per call and the prompt:completion ratio —
  derived entirely from the existing token_usage telemetry (no new
  metering). The output states the honest caveat inline: prompts
  include the user's own task text, so this is an upper bound on
  harness overhead, not the overhead itself; separating the injected
  step-contract/knowledge share would need per-segment metering that
  doesn't exist yet.
- **prose_rhythm v2 — burstiness, paragraph-CV, field-measurement
  corrections (#318)**: `low-burstiness` catches a locally flat beat
  (mean adjacent sentence-length delta / mean length) that
  document-wide CV misses — a slow short-to-long drift has variance
  but no alternation. `uniform-para` relaxes from exact sentence-count
  equality to a CV threshold, catching the "every paragraph is 2-3
  sentences" template tic. `taigendome_ratio` is reported
  informationally (never flagged): independent field measurement
  (7 models × 406 documents vs a 137-document human corpus) found
  AI-generated Japanese uses 体言止め at near-zero rates while humans
  mix it in — the *absence* is the signal, reversing the folk belief.
  The same measurement's corrections land in ai-writing-smells
  (attributed): sentence-initial repetition is a *human* habit (93% of
  human documents), and rhythm monotony varies sharply by model family
  ("clean vocabulary, monotone rhythm" exists — grounds for rejecting
  on the rhythm layer even when every vocabulary marker passes). The
  thresholds' honest status (uncalibrated heuristics; mora-based
  measurement impossible stdlib-only) is now stated in the docstring.
- **Stale path-reference check for the manifest/knowledge layer (#316)**:
  `workbench.py stale-refs [paths…]` scans `.claude/rig.md` and the
  project knowledge layer for backtick-quoted relative path references
  whose target no longer exists — the direct rot signal, next to the
  time-proxy freshness stamps (wiki `reviewed_at`, instinct decay).
  Deliberately conservative extraction (two-plus segments, extension or
  trailing slash, no URLs/absolute/placeholder tokens, code fences
  skipped; bare prose paths are out of scope by design) and
  ancestor-walk resolution (a doc may speak relative to any contextual
  root between its own directory and the repo root), so the
  false-positive rate on real docs is near zero. WARN-only, exit 0 —
  fixing or deleting a reference stays a judgment call. `validate.py`
  applies the same logic to rig's own 201 shipped docs via
  `check_stale_refs` (curated example-namespace excludes for paths that
  describe user projects or other repos), with a clean baseline.
- **Destructive-command sensor backing `no_destructive_operation` (#315)**:
  deterministic scan of the task diff (added lines + untracked files)
  for destructive command patterns, wired into every `gate` evaluation
  the same way the secret/injection sensors are. Unambiguous destroyers
  (`rm -rf /`, `mkfs`, `dd of=/dev/...`, `DROP DATABASE`) are
  fail-grade; context-dependent patterns (absolute-path/variable/`~`
  `rm -rf`, `git clean -f`, `git reset --hard`, `git push --force`
  without `--force-with-lease`, `DROP TABLE`/`TRUNCATE`,
  `chmod -R 777`) and mass deletions (>= 20 files vs base) are
  warning-grade. Relative-path `rm -rf build/` is deliberately not
  flagged (everyday-legitimate in clean targets). Explicit
  `--set no_destructive_operation=passed` is the recorded escape hatch
  (`destructive_override`). Standalone CLI: `scan-destructive`. Honest
  scope: detects destructive commands written into the diff — it does
  not intercept commands the agent executes at run time (that is the
  host permission system's job).

## [1.16.0] - 2026-07-16

### Added — issue-backlog sweep (#263–#307) + writing-quality layer

- **Cognitive-rhythm grounding + deterministic prose-rhythm sensor**:
  `knowledge/ai-writing-smells` gains a summarized (own-wording,
  attributed) cognitive-rhythm principle — dense prose reads as boring
  when the reader's cognitive mode never switches — plus compact
  practice rules (opening tension, section bridges, list landing,
  tension ledger, density waves, topic test). `scripts/prose_rhythm.py`
  (stdlib-only, deterministic) machine-measures the surface proxies:
  long-sentence runs, uniform sentence-length variance, ending
  repetition, uniform paragraph shapes, progress-narration phrases
  (topic-test deletion candidates), and connective density. Advisory
  by design — exit code never gates; the semantic judgment stays with
  `ai-smell-reviewer`, which now gets numbers instead of impressions
  (wired into de-ai-smell's detection step as an optional pre-pass).
- **`/rig:rig cockpit` — read-only Mission Control dashboard (#307)**:
  Aggregates run timeline, gate radar, drill-measured reviewer
  confidence, a cost meter, and a force-bypass safety strip onto one
  screen by reusing `board`/`stats`/`audit`/`confidence`'s existing
  aggregation functions (`read_all_tasks`, `gate_status_counts`,
  `aggregate_drill_confidence`, `force_bypass_counter`) — no new
  persistence, no duplicated logic. The cost meter reads the same
  `.rig/runs.jsonl` token-usage telemetry `orchestrate.py runs --cost`
  already produces (#271/#296, which didn't exist yet when this
  feature was originally designed). v1 is read-only: accept/discard
  stay in the existing commands, cockpit only recommends. Missing data
  (no drill run, no token usage recorded) is shown as "Unmeasured"
  rather than a blank that could be misread as healthy.
- **Continuous cross-session instinct-learning layer (#306)**:
  `workbench.py` gains an `instincts` subcommand managing
  `.rig/instincts.jsonl` (id/text/evidence/source_task_ids/confidence/
  first_seen/last_seen/hit_count/decay_reason/status/supersedes) —
  completely separate from `facets/knowledge`'s verified wiki.
  `--add` rejects secrets/tokens/absolute home-directory paths/
  `ENV_VAR=value`-shaped candidates outright, with the reason shown
  (never a silent drop). `--decay` lowers confidence by 0.1 for active
  instincts unused for 30+ days, expiring below 0.2 — implicit
  knowledge rots by design rather than accumulating forever. Conflict
  resolution is explicit, not inferred: recognizing two instincts
  contradict each other needs judgment, so `--supersedes <old-id>` is
  how the model declares it, which mutes the old one. Only confidence
  >= 0.7 gets selected for injection, capped at 500 chars total
  (`select_for_injection`), keeping context-minimal intact.
  `hooks/suggest-instincts.sh` (Stop) reminds the model to consider
  proposing a pattern — it doesn't extract one itself, since deciding
  what's durably useful is a judgment call the hook can't make; most
  sessions won't have anything worth recording.
  `hooks/inject-instincts.sh` (SessionStart) injects the selected
  instincts as `additionalContext`. Both wired into `hooks/hooks.json`
  without touching the existing inject-talk-mode.sh/
  preserve-rig-state.sh/remind-rig-header.sh hooks. Verified end-to-end
  in a disposable repo: secret-pattern rejection, supersedes-based
  muting excluding the old instinct from `--inject-preview`, decay
  after backdating `last_seen`, and CLI-level mute/expire/decay/list.
  Honest scope: automatic semantic contradiction *detection* isn't
  implemented — only the mechanical *resolution* once a contradiction
  is explicitly declared via `--supersedes`. Pattern extraction itself
  is left entirely to the model's judgment.
- **Read-only VS Code extension for rig board (#286)**:
  `vscode-extension/` shows `.rig/runs/` task/gate state in an Explorer
  sidebar panel, refreshed via a FileSystemWatcher. It's read-only by
  construction — no accept/discard or any other write command is
  registered. The state-parsing logic (`rigState.ts`) has no dependency
  on the `vscode` module, so it's unit-tested with plain Node; the
  gate-status priority order is ported to match `workbench.py`'s
  `gate_status()` exactly. Compiles cleanly against `@types/vscode`;
  actually loading it inside a live VS Code Extension Host is
  unverified in this environment (no VS Code GUI available here).
- **Experimental Managed Agents API backend for review fan-out (#295)**:
  `run_managed_agents_fanout()`, an opt-in alternative to the existing
  subprocess + ThreadPoolExecutor review-gate fan-out, delegates to
  Anthropic's Managed Agents API (coordinator/worker beta,
  `managed-agents-2026-04-01`) via raw urllib calls (no SDK dependency,
  consistent with orchestrate.py's stdlib-only stance). One worker
  agent per persona, a judgment-only coordinator, polled via
  `threads.list` until all workers report in. Returns the same shape as
  `run_verifiers_parallel` so `_execute_step`'s pass/fail logic is
  unchanged. Only used when `cfg["parallel_backend"] ==
  "managed-agents"` — the existing default path is completely
  untouched. Honest scope: the REST endpoint paths are inferred from
  the documented Python SDK method names, not confirmed against an
  official REST reference. Verified against a mock HTTP server
  reproducing the full call sequence (worker/coordinator creation,
  session creation, event send, threads polling, aggregation,
  environment_id-missing error path, unreported-worker timeout,
  connection failure) — not connected to the real API.
- **Signed provenance via HMAC-SHA256 on accept (#299)**:
  `accept` now writes `.rig/runs/<task_id>/provenance.json`
  (task_type/recipe/base/gate status/checks) signed with a
  locally-generated HMAC-SHA256 key (`.rig/provenance.key`,
  gitignored). `workbench.py verify-provenance <task_id>` checks the
  signature and exits 1 on mismatch or tamper. Scoped deliberately to
  HMAC rather than asymmetric signing (Ed25519/SLSA) to keep
  workbench.py's stdlib-only dependency policy — this gives
  same-machine tamper-evidence, not third-party public verification.
  Documented clearly in code and workbench-ops.md so it isn't mistaken
  for the heavier guarantee. Verified end-to-end in a throwaway repo:
  sign, verify (valid), tamper the record, re-verify (INVALID, exit 1).
- **Gap prescription now drafts a concrete `/rig:forge` request (#268)**:
  `orchestrate runs`' existing hot-gap detection (same recipe+step
  escalating 2+ times) now cross-references that step's recorded
  verdicts to name the top rejecting reviewers, and prints a
  ready-to-paste `/rig:forge "..."` request describing exactly what's
  failing (in addition to the existing `/rig:import --discover`
  suggestion). `orchestrate.py` doesn't invoke forge itself — that
  needs an LLM — it closes the gap between "detected" and "actionable"
  as far as a deterministic script can. Verified via `orchestrate
  selftest` with synthetic `runs.jsonl` data: two escalations on the
  same step with a rejecting reviewer produce a prompt naming that
  reviewer.
- **RBAC for accept and time/cost budget warnings (#282, #281)**:
  `.rig/access.json` (opt-in) restricts `accept` to an allowlist per
  task_type, identity resolved via the `RIG_USER` env var or `git
  config user.name`. Absent file = unrestricted, same as before.
  `--budget-minutes` on `workbench.py new`: `status`/`board` show a
  ⚠ marker past the estimate. Advisory only, never blocks. Both are
  additive and default to today's unrestricted behavior when their
  config is absent. Verified end-to-end in a throwaway repo: the
  budget marker shows in status/board, and RBAC blocks/allows accept
  correctly by identity.
- **Security/quality batch — secret masking, SAST adapter, rescan,
  flaky, observability bridge (#273, #274, #275, #276, #277, #278,
  #279)**:
  - `implement.md`: pre-generation secret scan before subagent
    dispatch, masking existing secrets rather than letting them into
    context.
  - `acceptance-check.md`: documents `no_suspicious_code_similarity`
    and `dependency_license_and_cve_checked` as opt-in criteria
    (enabled via `.rig/gates.json`'s `extra_criteria`), plus
    `sast_findings_clear` tied to the new adapter below.
  - `scripts/sast_adapter.py`: converts Semgrep-style JSON into a
    single worst-case-aggregated acceptance criterion (`workbench.py
    gate` rejects unregistered criterion names, so per-finding checks
    don't fit its model — one aggregate check does). Verified
    end-to-end in a throwaway repo.
  - `skill-import.md`: new `--rescan` mode re-scans already-imported
    bricks against the injection-patterns catalog independent of
    upstream diffs.
  - `verify.md`: distinguishes known-flaky test failures (rerun/CI
    history) from genuine regressions before marking
    `tests_pass_or_explained`.
  - `observability-reviewer.md` + `implement.md`: findings now carry
    concrete instrumentation suggestions, bridged into an implement
    step scoped to this task's diff only (no unrelated-code
    instrumentation sweeps).
- **Slack/Teams webhook notifications (#287)**: `scripts/notify.py`
  posts to Slack/Teams incoming webhooks via urllib only (no SDK
  dependency) — `--format slack|teams`, `--dry-run` to inspect the
  payload without sending, `RIG_NOTIFY_WEBHOOK` env var support.
  Verified against a local HTTP server for both formats plus dry-run
  and the no-webhook error path. Deciding whether/when an event
  warrants a notification stays the caller's job (the instruction
  layer), not this script's.
- **Cross-repository fleet aggregation (#272)**: `orchestrate.py
  fleet --repos p1,p2,...` reads `runs.jsonl` and
  `drill-results.jsonl` from multiple repos read-only, aggregating run
  counts and per-persona detection rate across projects, plus a
  per-repo breakdown to see where a given reviewer persona performs
  better or worse. `--anonymize` swaps repo paths for `repo-N` labels.
  No repo's `.rig/` data is written to.
- **Dogfooding section in the README (#284)**: documents how a
  maintainer measures rig's own gate efficacy with the existing
  `workbench.py digest --period month` / `stats` / `/rig:drill
  --replay` commands — no new tooling. Honest scope note: this repo
  doesn't auto-publish those numbers (no CI job regenerating a badge
  on merge); today "dogfooding" means running the commands locally.
- **Talk-mode structured logging and deja-vu detection (#292, #290)**:
  `talk-loop.md` step 7 captures decisions/confirmed-assumptions/
  open-questions from the requirement negotiation into `talk-log.md`
  (an unapproved log, same tier as `diff.md`) once a task-id exists.
  `workbench.py new`'s `find_similar_tasks()` scores past task inputs
  by Jaccard overlap on a rough tokenization (no embeddings/search
  engine), surfacing a "Similar tasks" section in the routing banner
  above a similarity threshold. Verified: a paraphrased duplicate task
  is caught, an unrelated task isn't.
- **Multi-recipe A/B experiment mode and streaming-gate guidance
  (#291, #302)**: `orchestrate.py ab <recipe1> <recipe2>...` runs the
  same goal through multiple recipe variants concurrently
  (ThreadPoolExecutor), each in its own isolated worktree via the
  existing `setup_isolation`/`teardown_isolation` path so variants
  never conflict. Reports elapsed time, retry count, and final status
  per variant; incomplete/dirty variants keep their worktree for
  inspection, same rule as `--isolate`. `implement.md` gains an
  opt-in note on streaming lightweight checks (type/lint only) at
  natural checkpoints during large (size L/XL) implementations, to
  reduce end-of-verify pileup — final pass/fail still comes from the
  normal acceptance-gate.
- **GitHub Action for headless CI usage (#265)**: `action.yml`
  (composite) wraps `orchestrate.py run --isolate` for workflows
  without a live Claude Code session. `scripts/rig-action-entrypoint.sh`
  derives the final status from the run-state JSON (`done`/`stopped`
  fields, the same logic `orchestrate.py` itself uses) and only pushes
  a branch + opens a PR via `gh pr create` when the gate resolved
  `DONE` — a failing or pending gate fails the job and creates
  nothing. Honest scope: verified the `run` path end-to-end locally
  with `--provider mock`; the `open-pr` path (branch push + `gh pr
  create`) needs a live GitHub Actions runner and isn't exercised
  here.
- **Static threat scan for rig's own MCP tools (#303)**: adds
  `orchestrate.py mcp-scan [--json]`, which statically analyzes
  `scripts/mcp_server.py`'s TOOLS definitions across three adversarial
  lenses (attacker/defender/auditor) for shell/network
  over-permission, plaintext secret exposure, and hook-injection risk.
  Never executes anything — reads the TOOLS dict and source text only,
  deterministic, no side effects. Module-level findings cover the
  shared subprocess/secret path; tool-level findings classify each
  tool as read/write and flag `rig_orchestrate_run` as MEDIUM severity
  since it can affect the main working tree directly when `--isolate`
  isn't set — with the concrete mitigation (`isolate: true`) spelled
  out. `validate.py`'s new `check_mcp_scan()` wires the overall
  verdict into CI (HIGH->FAIL, MEDIUM->WARN, LOW->PASS), silently
  skipping when `mcp_server.py` isn't present.
- **AST-based semantic diff summary for Python (#280)**:
  `scripts/ast_diff.py` compares Python source with the stdlib `ast`
  module (top-level/class-level def/class comparison) to distinguish
  signature changes, body-only changes, additions/removals, and
  cosmetic-only edits (identical AST despite differing text).
  `workbench.py diff` now inserts a "Semantic diff (Python)" section
  for Modified `*.py` files, augmenting rather than replacing
  `diff.md`'s prose Summary. Non-Python/unparseable files simply don't
  get this section and fall back to the existing text diff.
- **Confidence-weighted gate via drill detection rate (#301)**: new
  `workbench.py confidence [<task_id>]` surfaces drill-measured
  detection rate per reviewer as a supplementary signal alongside the
  existing pass/fail gate — task-scoped calls record
  `reviewer_confidence` into `acceptance.json` without touching gate
  logic itself. Below a 70% threshold it's flagged low-confidence and
  an additional reviewer is suggested, never auto-dispatched.
  Unmeasured personas stay "unmeasured" rather than a fabricated score.
  `aggregate_drill_confidence()` is a shared, pure aggregation function
  so nothing re-derives it independently.
- **Fable 5 refusal-classifier and server-side fallback handling
  (#297)**: a new `anthropic` provider (`run_anthropic_provider`) calls
  the Anthropic Messages API directly over HTTP — the `claude`/`rig`
  CLI providers use `--output-format text` and never expose a
  structured `stop_reason`, so this is a separate code path. On
  `stop_reason: "refusal"` with no fallback content block, records
  `FABLE_REFUSAL` (category/explanation) in state history and returns
  rc=1 (not a silent failure). When a `{"type": "fallback"}` content
  block is present (the `server-side-fallback-2026-06-01` beta
  succeeded), records `FABLE_FALLBACK` and treats the step as a normal
  success — the gate is not blocked. Usage
  (input/output/`cache_read_input_tokens`) is normalized into the
  existing #271/#296 token_usage accumulator and surfaced in `runs
  --cost`, alongside a fallback/refusal occurrence count.
  `agents/security-reviewer.md` and `commands/orchestrate.md` now warn
  that assigning Fable 5 to attack-technique-focused personas via
  `--step-model` (#293) requires setting `fallback_model`. Honest
  scope: verified against a mock HTTP server reproducing the Anthropic
  Messages API's response shape — not connected to the real Anthropic
  API (billing/live-traffic risk).
- **Host adapter layer generalizing native-layer integration beyond
  Codex (#304)**: `scripts/host_adapters.py` centralizes per-host
  differences (hook event names, skill path conventions, capability
  matrix, degrade behavior) into a single `HOSTS` dict, so adding a new
  host means adding one entry rather than touching rig's core. Cursor
  was added as the second host to validate the design (researched
  against Cursor's official docs): hook event names are camelCase
  (`PreCompact` -> `preCompact`), Cursor reads `.agents/skills/` for
  legacy compatibility so Codex's existing `SKILL.md` works unmodified,
  and `preCompact` is documented as observational-only — `cursor/hooks.json`
  declares this as an honest degrade instead of pretending it works.
  Claude Code's and Codex's existing files are unchanged; the adapter
  only maps to what's already shipped.
- **Codex CLI native-layer integration (#294)**: `codex/skills/rig/SKILL.md`
  (Codex's `.agents/skills/<name>/SKILL.md` convention, a thin pointer to
  the existing `workbench.py`/`orchestrate.py` — no new engine),
  `codex/hooks.json` (`PreCompact` wired to the existing
  `hooks/preserve-rig-state.sh`, reused as-is), and
  `.codex/agents/security-reviewer.toml` (a Codex-native subagent
  mirroring `agents/security-reviewer.md`'s review axes and output
  contract, with `sandbox_mode = "read-only"` layered on top of
  `orchestrate.py`'s existing argv-level read-only enforcement — defense
  in depth, not a replacement). Honest scope: this environment has no
  codex CLI, so none of it has been exercised live — hooks.json validates
  as JSON and the TOML parses with `tomllib` using only documented
  fields, but actual skill loading, hook firing, sandbox enforcement, and
  MCP connection are unverified. The existing stateless `--provider
  codex` path is untouched.
- **Production outcome feedback loop (#289, #300)**: `accept` lands a
  staged diff, so workbench never sees the final commit SHA a human
  creates. `workbench.py record-commit <task_id> [<sha>]` links
  task_id -> sha explicitly. `record-outcome <task_id> --status
  ok|incident` logs what actually happened in production — the
  real-world counterpart to drill's synthetic detection rate.
  `trace-commit <sha>` reverse-looks-up a sha to its task, shows the
  original gate prediction plus any recorded outcome, and drafts a
  revert plan (command + PR title/body) when the outcome is
  "incident" — it never creates the PR or runs the revert itself,
  that stays a human/GH-tool step.
- **Learned auto-router from historical run data (#305)**:
  `learned_auto_route()` aggregates `.rig/runs.jsonl`'s track record
  (which model actually got used per recipe/step, and did the step
  pass) and picks the cheapest static `--auto-route` (#264) candidate
  meeting a pass-rate/sample threshold — frequency-based, no ML model.
  Defaults to shadow mode: predictions are always recorded
  (`LEARNED_ROUTE_PREDICTION` in history, `steps[].learned_route` in
  telemetry) but only applied when `--auto-route-mode active` is set.
  Insufficient samples or low pass rate falls back to the static
  auto-route, with every rejected candidate and its reason recorded
  (counterfactuals) so the choice stays auditable.
  `--exploration-pct`/`--exploration-date` let a deterministic fraction
  of runs try the next-cheapest candidate (hash-based, no randomness).
  Honest scope: regret logging (auto-calibrating "too cheap"/"too
  expensive" picks) is not implemented.
- **Cost-tier auto-routing (#264)**: recipe steps can declare
  `auto_route.candidates` (`{model, cost_tier, max_size}`, cheapest
  first). `orchestrate.py run --auto-route` resolves the first candidate
  whose `max_size` covers the measured diff size (reusing the existing
  `size_class`/`git_diff_lines`/manifest machinery), falling back to the
  most capable candidate if none fit. It's a fallback only — runtime
  `--step-model` and the recipe's own `model:` both still win outright.
  The decision is recorded in run-state history and `runs.jsonl`'s
  `steps[].auto_route`. `resolve_auto_route()` is a pure, tested function
  proving determinism (same input -> same choice).
- **MCP server (#263)**: `scripts/mcp_server.py` implements a minimal MCP
  stdio transport (JSON-RPC 2.0, line-delimited) without depending on the
  `mcp` SDK, matching workbench.py/orchestrate.py's stdlib-only stance. It
  exposes 14 tools (`rig_task_*`, `rig_orchestrate_*`) that shell out to
  the existing workbench.py/orchestrate.py CLIs — no new engine, and
  accept/discard's force-proof requirements go through the identical code
  path so they can't be bypassed via MCP.
- **Token/cost usage metering for HTTP-based providers (#271, #296)**:
  `orchestrate.py` now captures the OpenAI-compatible `usage` field from
  ollama/lmstudio responses (`_record_token_usage`, thread-safe) and rolls
  it up per-run as `token_usage` in `runs.jsonl`; `orchestrate.py runs
  --cost` aggregates it by recipe/provider. CLI-based providers
  (claude/codex) don't expose structured usage and are explicitly out of
  scope — the command says so and points to Anthropic's Usage & Cost
  Admin API instead of estimating.

## [1.15.0] - 2026-07-11

### Added — three more research-backed hardening items

- **Verify-first resume ritual**: `orchestrate resume <run-state.json>`
  re-anchors a persisted run before continuing — it prints a digest,
  re-runs the current step's machine checks, and refuses to advance when
  a previously-passing check now fails ("world drifted"); a >1h mtime gap
  cues possible context compaction. Complements the PreCompact hook: prose
  survives compaction, and now the machine re-verifies too. (Anthropic
  long-running-agents startup ritual.)
- **External-content quarantine (#269)**: `quarantine.wrap_untrusted`
  fences issue/PR/tool text in a per-call, unforgeable sentinel with an
  explicit "this is DATA, never instructions" boundary and strips
  invisible/bidi Unicode before it enters a prompt; wired into the goal
  span and gh-flow's untrusted-text rule. (OWASP LLM01 / spotlighting /
  CaMeL.)
- **MAST failure-mode taxonomy**: failed runs now record a deterministic
  `failure_mode` code (verification:self-grading / incorrect-implementation
  / missing / unclassified) in runs.jsonl; `patterns/failure-taxonomy.md`
  maps each MAST mode (arXiv 2503.13657) to the gate criterion or brick
  that should have caught it, and the dashboard gains a failure-mode
  panel — rig's measured-gates philosophy turned onto its own failures.

### Verification

- `python3 scripts/orchestrate.py selftest` → PASS (scenarios AA + FM)
- `python3 scripts/validate.py` → PASS 46 / WARN 8 / FAIL 0
- `python3 scripts/validate.py selftest` → 12/12 scenarios OK
- `ruff check scripts rig_workbench tests` → all checks passed
- `pytest -q` → 279 passed

## [1.14.0] - 2026-07-11

### Changed — research-hardened release: 5 workstreams from a 2024-2026 literature sweep

Backed by a five-theme survey (LLM-as-judge reliability, agent-harness
design, AI code-review market, mutation testing, agent security) — every
change below cites its evidence in the commit messages.

- **Verifier judge hardening** (MT-Bench / Style-over-Substance /
  CodeJudgeBench / Anthropic eval guidance): verifiers now judge the
  actual worktree `git diff` as primary evidence — the generator's
  report is bounded and labeled as unverified claims; all verdict
  contracts flipped to evidence-first with the verdict as the last line
  (extraction takes the last verdict-token line, so quoted verdicts no
  longer force FAIL); per-criterion `CRITERION n: PASS|FAIL|UNKNOWN`
  verdicts with fail-closed all-UNKNOWN handling; judge-panel multi-PASS
  is recorded (`order_sensitive` + pass set) instead of silent
  first-PASS-wins; 30k-char output budget with spooled full text.
- **Anti-tamper gate sensor** (`no_gate_tampering`, METR reward-hacking
  evidence): edits to `.rig/gates.json`, `.rig/recipes/`, or CI
  workflows inside the task diff fail the gate; test modification/
  deletion, assert-removal, and skip-markers warn on bugfix/feature.
- **Injection-marker sensor** (`no_injection_markers`, Rules-File-
  Backdoor evidence): invisible/bidi Unicode fails (rendered only as
  U+XXXX escapes), instruction-override phrases warn; scans the diff
  plus repo prose surfaces; `scan-injection` standalone subcommand.
- **Manifest consent gate**: `.claude/rig.md` (repo-controlled, drives
  hook-eval'd commands and recipe search tiers) now uses the recipe
  trust store — soft-degrade to "no manifest" when untrusted; the git
  hooks verify the hash before eval; `githooks install` records consent.
- **Drill science** (selective-mutation literature): clean no-bug
  control diffs measure per-persona `clean_fp_rate`; finding-verifier
  screens seeds for the equivalent-mutant problem (`invalid_seeds`);
  seed catalog gains CWE/ODC provenance and 8 rows (XSS, path traversal,
  hard-coded secret, deserialization, missing authn, resource
  exhaustion, off-by-one, TOCTOU); Wilson 95% intervals for n<10 and
  history-aggregated persona-update triggers.
- **Review market mechanisms** (Bugbot dismissal-learning 52->80%,
  Anthropic Code Review knobs): `.rig/review-suppressions.jsonl`
  records verifier-refuted findings as injectable non-issues (an UPHELD
  finding always beats a suppression); severity-gated comment policy
  (nit cap 5 + rollup, Pre-existing marker, Important-only re-reviews).

### Verification

- `python3 scripts/orchestrate.py selftest` → PASS (incl. new scenario Y)
- `python3 scripts/validate.py` → PASS 46 / WARN 8 / FAIL 0
- `python3 scripts/validate.py selftest` → 12/12 scenarios OK
- `ruff check scripts rig_workbench tests` → all checks passed
- `pytest -q` → 241 passed

## [1.13.0] - 2026-07-11

### Added — issue-backlog sweep: 6 features from the roadmap triage

- **Project-level custom gate criteria** (#283): `.rig/gates.json` lets a repo
  add criteria to gate presets/task types — additive only (removal-shaped keys
  are rejected as a security posture), hard-erroring on typos before any run
  state exists. Project criteria carry a `[project]` tag in status/gates.
- **OpenAPI schema-diff sensor** (#288): `public_api_changes_documented` is now
  machine-backed — base-vs-worktree operation diff (paths/methods/params/
  responses, stdlib-only), warning-grade when diff.md is silent about a
  changed API, clean skip when no schema exists.
- **Deterministic secret scanner** (#273, scanner core): `workbench.py
  scan-secrets [paths|--diff <task-id>]` detects AWS/PEM/GitHub/Slack/OpenAI/
  Anthropic/Google/JWT patterns plus an entropy heuristic with a lockfile
  allowlist; findings are always masked. The gate sensor fails
  `no_secret_leak` on findings in the task diff (untracked files included);
  `--set no_secret_leak=passed` is the recorded escape hatch.
- **Git-hook distribution of machine sensors** (#298): `rig-wb githooks
  install|uninstall|status` ships signed pre-commit (manifest lint + staged
  secret scan) and pre-push (build + test) hooks; foreign hooks are never
  overwritten without --force; `RIG_HOOK_SKIP*` env bypasses per check.
- **Telemetry digest** (#285): `workbench.py digest [--period week|month]`
  renders a Markdown digest (runs, gate pass/fail + most-failed criteria,
  force accepts, rubber-stamp suspects, drill detection rate), reusing the
  stats helpers.
- **Per-step model override** (#293): `orchestrate run ... --step-model
  <step-id>=<model>` (repeatable); precedence runtime > recipe `model:` >
  `--model`; unknown step ids abort pre-run; the actually-used model is
  recorded in run-state and runs.jsonl for cost attribution.
- **Drill coverage check + gate-efficacy panels** (#266, scoped): the
  validator now WARNs for gate-bearing shipped recipes whose reviewers
  /rig:drill cannot exercise (16 flagged today); the dashboard gains a
  detection-rate sparkline and a per-criterion gate-failure table.
- **Positioning docs** (#267) and **gc/audit routing** (#261, #262): the
  implemented-but-unroutable workbench subcommands are wired into the command
  docs; §1 documents rig's thin-layer + external-control-plane positioning.

### Housekeeping

- Closed 20 stale auto-filed issues (#213-#250 range) after verifying each
  against the current code with file:line evidence — all had been implemented
  between v0.97.0 and v1.12.0.

### Verification

- `python3 scripts/orchestrate.py selftest` → PASS (96 [OK] incl. scenario Z)
- `python3 scripts/validate.py` → PASS 46 / WARN 8 / FAIL 0 (8 WARNs = new
  drill-coverage findings, intentionally surfaced)
- `python3 scripts/validate.py selftest` → 12/12 scenarios OK
- `ruff check scripts rig_workbench tests` → all checks passed
- `pytest -q` → 158 passed

## [1.12.0] - 2026-07-10

### Changed — the remaining self-application debt from 1.11.0

- **workbench.py and validate.py split**: both monoliths (1,195 and 975 lines)
  now follow the same package pattern as orchestrate — `scripts/*.py` are
  21-line shims; implementations live in `rig_workbench/workbench/` (six
  modules) and `rig_workbench/validation/` (eight modules). Outputs verified
  byte-identical, including an end-to-end scratch-repo accept/discard flow.
- **CHANGELOG slimmed**: pre-1.10 entries (111 sections, ~197KB) moved to
  `docs/CHANGELOG-archive.md`; the top-level file keeps the current line.

### Fixed

- **skills-lock provenance**: all 18 hyperframes imports now record
  `importedAs: facets/instructions/render-hyperframes.md` + `mode: delegate`,
  per the family-level delegation documented in skill-import.md §3 and
  CHANGELOG v0.36.0. Resolves the 18 standing validate WARNs — the validator
  now reports WARN 0.
- **API contracts** (found while writing the 1.11.0 test suite):
  importing `recipes.py` without PyYAML no longer exits at import time;
  the cross-project telemetry mirror path is `config.GLOBAL_RUNS_PATH`
  (rebindable instead of an unavoidable `~/.rig` write); `plan --json`
  exits 1 on plan errors like the non-JSON path; `queue_set_status`
  returns whether the item was found instead of silently no-oping.

### Verification

- `python3 scripts/orchestrate.py selftest` → PASS (90 [OK])
- `python3 scripts/validate.py` → PASS 45 / WARN 0 / FAIL 0
- `python3 scripts/validate.py selftest` → 9/9 scenarios OK
- `ruff check scripts rig_workbench tests` → all checks passed
- `pytest -q` → 58 passed

## [1.11.0] - 2026-07-10

### Changed — self-application release: the quality bar rig sells now applies to rig itself

- **Monolith split**: `scripts/orchestrate.py` (2,781 lines) is now a 21-line
  compatibility shim; the implementation lives in `rig_workbench/orchestrate/`
  as ten cohesive modules (config / recipes / runstate / providers / isolate /
  queueing / graph / selftest / commands / cli). Selftest output is
  byte-identical to the pre-split baseline.
- **English everywhere in code**: all CLI output, comments, docstrings, help
  text, installer messages, CI workflow comments, and hook-injected directives
  are now English. The Japanese review-verdict protocol token and
  full-width-colon condition regexes are preserved as escaped literals — they
  are live wire-format contracts.
- **Main entry renamed to `/rig:go`**: `/rig:rig` remains as a compatibility
  alias. Experimental commands (magi, sage, roast, coin, duck, pre-mortem,
  party, movie, scenario) are now marked `[experimental]` in their
  descriptions. `plugin.json`'s description shrank from 2,192 to 384 chars.

### Added

- **Project-recipe trust gate**: recipes under `<cwd>/.rig/recipes/` (which can
  overlay shipped recipes and whose `checks:` run as shell commands) now
  require one-time explicit consent — `--allow-project-recipes` or
  `RIG_ALLOW_PROJECT_RECIPES=1` — recorded as a content hash in
  `~/.claude/rig/trusted-recipes.json` (`RIG_TRUST_STORE` overrides). An
  edited file re-requires consent. Covers name resolution, explicit overlay
  paths, and `extends`-chain parents.
- **pytest suite**: 54 unit tests (`tests/`) covering recipe resolution,
  run-state/gate transitions, queue backends, brick-graph shape, CLI smoke,
  and the trust gate — all asserts on machine tokens, sandboxed via tmp_path.
- **CI hardening**: `validate.yml` now also runs `ruff check` (0 findings,
  down from 65) and the pytest suite. `validate.py` gained a version-sync
  check across plugin.json / pyproject.toml / `rig_workbench/__init__.py`.

### Verification

- `python3 scripts/orchestrate.py selftest` → PASS (90 [OK], byte-stable)
- `python3 scripts/validate.py` → PASS 45 / WARN 18 / FAIL 0
- `python3 scripts/validate.py selftest` → 9/9 scenarios OK
- `ruff check scripts rig_workbench tests` → all checks passed
- `pytest -q` → 54 passed

## [1.10.6] - 2026-07-08

### Fixed — verifier が review-verdict 契約を解釈できるようにした

- **`scripts/orchestrate.py` の verifier パースを両契約対応に**：`_verdict_ok` を新設し、machine verdict (`VERDICT: PASS/FAIL`) に加えて review-verdict contract (`判定: APPROVE / APPROVE_WITH_CONDITIONS / REJECT`) を正しく解釈するようにした。これまで reviewer 系ペルソナ（security-reviewer / design-reviewer / test-reviewer 等）の判定が machine verdict しか見ていない grep で全て FAIL 扱いになっていた不具合を解消。`_build_verify_prompt` も「最後の1行だけに `VERDICT:` を出す」よう厳格化してパース揺れを減らした。
- **`resolve_http_model` の endpoint 不整合**：`--base-url` 明示時に、別 endpoint の保存 default が返ってしまう不具合を修正。endpoint が一致するときだけ保存 default を使う。
- **`build_argv` の codex verifier で `--sandbox read-only` を二重指定していたのを削除**：上流で既に `read-only` を明示しているので `_READONLY_ENFCE` の追加は冗長だった。
- **`run_provider` は非0 exit 時に stderr もマージして返す**：verifier note に原因が残るようにしてデバッグ性を上げた。
- **`cmd_selftest` の期待値を更新**：codex generator が `workspace-write` サンドボックスを明示するようになった状態に selftest を追従。

### Added — `max-bugfix` の acceptance を機械チェックで締める

- **`max-bugfix` recipe の `acceptance` に `checks:` を追加**：`.py` diff 存在確認、`git diff --check`、`pytest` を機械強制。LLM の受け入れ判定に加えて計算的センサーで締めるようにした。

### Verification

- `python3 scripts/validate.py` → PASS
- `python3 -m compileall scripts/orchestrate.py` → PASS
- `python3 scripts/orchestrate.py selftest` → PASS

## [1.10.5] - 2026-07-07

### Added — mock benchmark の再現性と retry 学習の改善

- **`scripts/orchestrate.py` の prompt に retry 文脈を注入**：`attempt`、直近の `history`、前回失敗理由を step prompt に含めるようにした。再試行が同じ指示の繰り返しになりにくくなり、`max-bugfix` での収束が安定する。
- **`mock` provider を task-aware な oracle として拡張**：`implement` step では task ごとに対象ファイルへ実際の修正を入れるようにし、`max-bugfix` の built-in mock benchmark で `spec=PASS` を再現可能にした。

### Verification

- `python3 scripts/validate.py` → PASS
- `python3 -m compileall scripts/orchestrate.py` → PASS
- `python3 -m rig_workbench.cli bench --provider mock --mode both --rig-recipe max-bugfix --tasks all --runs 1 --max-steps 14 --out /tmp/rig-max-bugfix-both-recheck.json --html /tmp/rig-max-bugfix-both-recheck.html` → `rig` 側が全タスク `spec=PASS` を再現

## [1.10.4] - 2026-07-07

### Added — retry 文脈を prompt に注入

- **`scripts/orchestrate.py` の implement/test 再試行を強化**：各 step の `attempt`、直近の `history`、前回失敗理由を prompt に含めるようにして、再試行が同じ文面の繰り返しにならないようにした。`fast-bugfix` / `max-bugfix` で no-op を止めた後、次の改善点である「失敗から学ぶ」層を足す。

### Verification

- `python3 scripts/validate.py` → PASS
- `python3 -m compileall scripts/orchestrate.py` → PASS
- `python3 -m rig_workbench.cli bench --provider mock --mode rig --rig-recipe max-bugfix --tasks divide-by-zero --runs 1 --max-steps 4 --out /tmp/rig-max-bugfix-rerun2.json --html /tmp/rig-max-bugfix-rerun2.html` → `calls=2` で no-op 防止が維持されることを確認

## [1.10.3] - 2026-07-07

### Added — `max-bugfix` の shipped 追加

- **`max-bugfix` recipe を追加**：`bugfix` を土台に、`implement` で diff と `git diff --check` を強制し、`test` で `pytest` を強制する、より堅い修正フローを shipped した。`fast-bugfix` は速さ優先のまま残し、確実性を最優先したいときの選択肢を分けた。
- **`skills/rig` の目録を更新**：dev-core recipe を 8 件に更新し、新しい強い既定を catalog に載せた。

### Verification

- `python3 scripts/validate.py` → PASS
- `python3 -m compileall scripts rig_workbench` → PASS
- `python3 -m rig_workbench.cli bench --provider mock --mode rig --rig-recipe max-bugfix --tasks all --runs 1 --max-steps 4 --out /tmp/rig-max-bugfix-mock-all.json --html /tmp/rig-max-bugfix-mock-all.html` → `calls=2` で `inspect→reproduce` まで進み、`implement` の no-op は素通りしないことを確認

## [1.10.2] - 2026-07-07

### Fixed — fast-bugfix の no-op 実行と gate 判定

- **`fast-bugfix` に step-level checks を追加**：`implement` は `.py` の差分が出ないと合格しない、`test` は `pytest` を実行しないと合格しないようにした。これで「読んで終わり」の空振りを止める。
- **`scripts/orchestrate.py` の gate 判定を修正**：`gate` が無い step でも `checks` を優先して評価するようにし、計算的センサーを持つ step が素通りしないようにした。

### Verification

- `python3 scripts/validate.py` → PASS
- `python3 -m compileall scripts/orchestrate.py` → PASS
- `python3 -m rig_workbench.cli bench --provider mock --mode rig --rig-recipe fast-bugfix --tasks all --runs 1 --max-steps 4 --out /tmp/rig-fast-bugfix-mock-final.json --html /tmp/rig-fast-bugfix-mock-final.html` → `calls=1` まで落ち、implement の no-op が停止することを確認

## [1.10.1] - 2026-07-07

### Added — bench と shipped recipe の強化

- **`rig-wb bench` に leak 検出を追加**：bare / rig の両モードについて、実行前後の git status を比較して task 外の変更を `workspace_leaks` として記録するようにした。`--leak-check-root` で比較対象ルートを切り替えられる。
- **`rig-wb bench` の rig 実行を scratch cwd 対応に整理**：Codex / Claude の provider 呼び出しを scratch task ディレクトリで走らせ、rig 側は repo root を `PYTHONPATH` に足して `python -m rig_workbench.cli` を scratch から呼べるようにした。これで bare vs rig の比較条件を揃えやすくした。
- **`fast-bugfix` recipe を shipped 追加**：小粒バグ修正用の軽量フローを足し、`skills/rig` の目録にも反映した。既存の heavy な dev-flow ではなく、implement → test → acceptance だけに絞った最短経路の入口。
- **`scripts/orchestrate.py` の step 契約を強化**：implement / test / acceptance で期待する行動と報告項目を分け、少なくとも「読むだけ」で終わりにくい prompt にした。Codex generator には `workspace-write` sandbox を明示し、実際にファイルを書ける前提を揃えた。

### Verification

- `python3 scripts/validate.py` → PASS
- `python3 /home/itoshun/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/rig` → PASS
- `python3 -m compileall rig_workbench scripts` → PASS
- `python3 -m rig_workbench.cli bench --provider codex --mode rig --rig-recipe fast-bugfix --tasks divide-by-zero --runs 1 --max-steps 4` → runner exit 0 / leaks 0 / task 側の修正は未達（次の改善対象）

## [1.10.0] - 2026-07-07

### Added — `rig-wb bench` のタスク拡張と HTML dashboard

- **組み込み bench task を 4 件へ拡張**：既存の `divide-by-zero` / `order-dedup` に加えて、security 観点の `sql-inject` と refactor 観点の `dry-refactor` を追加。単純な test pass だけでは拾いにくい SPEC 準拠を測る。
- **`--max-steps` 既定を 14 に変更**：bugfix recipe が review / acceptance 側まで届きやすい設定に寄せた。旧既定 7 は実装途中で切れやすかった。
- **`--html <path>` を追加**：bench 結果 JSON から単一 HTML dashboard を生成。平均 elapsed / calls / test pass 率 / spec pass 率と、task 別の bare vs rig 比較表を表示する。外部依存なし。
- **Codex skill としての入口を追加**：`skills/rig` を `~/.codex/skills/rig` から読ませる運用を明記し、`$rig` を `/rig:rig` 相当として使えるよう `SKILL.md` / `agents/openai.yaml` / README を更新。

### Verification

- `python3 /home/itoshun/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/rig` → PASS
- `python3 /home/itoshun/.codex/skills/.system/skill-creator/scripts/quick_validate.py /home/itoshun/.codex/skills/rig` → PASS
- `python3 scripts/validate.py` → PASS 41 / WARN 18 / FAIL 0
- `python3 -m compileall rig_workbench scripts` → PASS
- `python3 -m rig_workbench.cli bench --mode bare --provider mock --tasks all --out /tmp/rig-bench-1.10.0.json --html /tmp/rig-bench-1.10.0.html` → 4/4 tasks test PASS / spec PASS

---

Older entries (1.9.0 and earlier) live in [docs/CHANGELOG-archive.md](./docs/CHANGELOG-archive.md).
