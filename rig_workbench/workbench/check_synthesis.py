"""Turn instincts into `checks:` entries a recipe can run (T8).

An instinct is a rule the harness could not enforce, which is why someone had to
remember it. Its natural end state is not prose injected into a prompt but a command
that fails when the rule is broken. This module does that conversion and writes the
result into a project recipe.

**What this can and cannot do.** Deciding that a sentence describes a mechanically
detectable condition, and what would detect it, is judgment. Code cannot read
"always pass --safe-mode" and derive a grep. So the conversion runs off a table of
recognized shapes: an instinct matches a rule or it produces nothing. Most will
produce nothing, and the report says how many — a synthesis pass that silently
covered 6 of 40 candidates would read as "the other 34 were fine".

**What the generated checks are.** Conservative and read-only: they grep the tracked
tree or inspect the environment, and exit non-zero when the condition the instinct
warns about is present. None of them modify anything. They are still generated from
records the store itself labels unverified, so a check that fires may be reporting a
wrong instinct rather than a real defect — which is why `--dry-run` exists and why
what gets written is a project recipe, never a shipped one.
"""

import dataclasses
import pathlib
import re

from .instincts import (TIER_HOST, TIER_PROJECT, _load_tiered,  # noqa: F401
                        _INSTINCT_CONFIDENCE_THRESHOLD)


@dataclasses.dataclass(frozen=True)
class CheckRule:
    id: str
    #: every pattern must appear in the instinct text for the rule to apply. Requiring
    #: all of them is what keeps a rule from claiming an instinct that merely shares a
    #: word with it.
    requires: tuple[str, ...]
    command: str
    why: str


# Derived from instincts that were classified by hand as shell-detectable. Each command
# exits 0 when the tree is clean and non-zero when the condition the instinct warns
# about is present, which is the polarity `_run_step_checks` expects.
RULES: tuple[CheckRule, ...] = (
    CheckRule(
        id="claude-p-needs-safe-mode",
        requires=(r"claude\s+-p", r"--safe-mode"),
        command="! git grep -nIE 'claude +-p' | grep -v -- '--safe-mode' | grep -q .",
        why="a subprocess `claude -p` without --safe-mode lets the Stop hook force an "
            "extra turn, so the captured answer is a hook message rather than a result",
    ),
    CheckRule(
        id="no-gh-pr-merge-auto",
        requires=(r"gh pr merge", r"--auto"),
        command="! git grep -nIE 'gh pr merge[^|]*--auto' | grep -q .",
        why="auto-merge is disabled on this repo, so --auto merges immediately instead "
            "of waiting for CI",
    ),
    CheckRule(
        id="no-gh-pr-merge-admin",
        requires=(r"gh pr merge", r"--admin"),
        command="! git grep -nIE 'gh pr merge[^|]*--admin' | grep -q .",
        why="--admin is refused by the harness classifier, so a script using it cannot "
            "complete unattended",
    ),
    CheckRule(
        id="no-gh-pr-checks-all-over-empty",
        requires=(r"gh pr checks", r"all\("),
        command="! git grep -nIF 'gh pr checks' | grep -F 'all(' | grep -q .",
        why="jq's `all` returns true for an empty array, so a wait loop written this way "
            "exits immediately while checks are still being registered",
    ),
    CheckRule(
        id="runtime-security-tests-unset-claudecode",
        requires=(r"CLAUDECODE", r"pytest"),
        # Static, like every other rule. The obvious form of this check —
        # `test -z "${CLAUDECODE:-}"` — asserts on the ambient environment, and rig's
        # default launch path *is* a Claude Code session, so writing it into a recipe
        # would make that step's gate fail on every run. The instinct is a precondition
        # for one command, not an invariant of the step.
        command="! git grep -nIE 'pytest[^|]*test_runtime_security' "
                "| grep -v 'env -u CLAUDECODE' | grep -q .",
        why="those tests assert on a guard that reads CLAUDECODE / "
            "CLAUDE_CODE_SESSION_ID, so a command that runs them without unsetting both "
            "fails for a reason that has nothing to do with the code",
    ),
    CheckRule(
        id="pack-persona-declares-inject",
        requires=(r"persona", r"inject:"),
        command="! git ls-files 'packs/*/facets/personas/*.md' "
                "'packs/*/facets/personas/*/*.md' | xargs -r grep -L 'inject:' | grep -q .",
        why="a persona that inlines its criteria instead of injecting a wiki page breaks "
            "the persona=judgment / wiki=fact split the packs are meant to keep",
    ),
)


@dataclasses.dataclass(frozen=True)
class Synthesized:
    instinct_id: str
    tier: str
    rule: CheckRule


def synthesize(root: pathlib.Path, min_confidence: float | None = None
               ) -> tuple[list[Synthesized], list[dict]]:
    """`(matched, unmatched)` over the active instincts at or above `min_confidence`.

    `unmatched` is returned rather than dropped so the caller can report it. Instincts
    that no rule recognizes are the majority and are the honest measure of how far this
    conversion actually reaches.
    """
    floor = _INSTINCT_CONFIDENCE_THRESHOLD if min_confidence is None else min_confidence
    merged, _project, _host = _load_tiered(root)
    matched: list[Synthesized] = []
    unmatched: list[dict] = []
    seen_rules: set[str] = set()
    for tier, rec in merged:
        if rec.get("status") != "active" or rec.get("confidence", 0) < floor:
            continue
        rule = _first_matching_rule(rec.get("text") or "")
        if rule is None:
            unmatched.append(rec)
        elif rule.id not in seen_rules:
            # Two instincts can describe the same condition — the duplicate across tiers
            # found in this repo's own store is exactly that — and the check only needs
            # to exist once.
            seen_rules.add(rule.id)
            matched.append(Synthesized(instinct_id=rec["id"], tier=tier, rule=rule))
    return matched, unmatched


def _first_matching_rule(text: str) -> CheckRule | None:
    for rule in RULES:
        if all(re.search(p, text, re.IGNORECASE) for p in rule.requires):
            return rule
    return None


class RecipeEditError(Exception):
    pass


def add_checks_to_recipe(path: pathlib.Path, step_id: str | None,
                         commands: list[str]) -> list[str]:
    """Insert `commands` into one step's `checks:` list, returning those actually added.

    Edits the text in place rather than round-tripping the YAML: a recipe is
    hand-written and re-serializing it would drop comments and reflow every unrelated
    line, turning a two-line addition into a whole-file diff nobody can review.
    """
    if not path.exists():
        raise RecipeEditError(f"recipe not found: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    fm_end = _frontmatter_end(lines, path)
    # Only inside the frontmatter. A recipe's prose commonly shows a YAML example in a
    # fenced block, and `- id:` in that example is not a step: scanning the whole file
    # made the default (`--step` omitted, so the *last* match wins) target the
    # documentation, write checks into it, and report success.
    starts = [(i, ln) for i, ln in enumerate(lines[:fm_end])
              if re.match(r"^(\s*)- id:\s*\S", ln)]
    if not starts:
        raise RecipeEditError(f"no steps found in {path} (expected a `- id: <step>` entry)")
    if step_id:
        starts = [(i, ln) for i, ln in starts
                  if re.match(rf"^\s*- id:\s*{re.escape(step_id)}\s*$", ln)]
        if not starts:
            raise RecipeEditError(f"step '{step_id}' not found in {path}")
    index, header = starts[-1] if not step_id else starts[0]
    indent = " " * (len(header) - len(header.lstrip()) + 2)

    end = fm_end
    for j in range(index + 1, fm_end):
        if re.match(r"^\s*- id:\s*\S", lines[j]):
            end = j
            break
    block = lines[index:end]

    # Compare rendered lines, not loosely-stripped text. `.strip('"')` left the wrapping
    # quotes on a command written in single quotes, so it never matched the copy already
    # in the file and was appended again on every run; `lstrip("- ")` also eats a leading
    # `-` from a command that begins with one.
    existing = {ln.strip() for ln in block}
    to_add = [c for c in commands if _render(c, indent).strip() not in existing]
    if not to_add:
        return []

    rendered = [_render(c, indent) for c in to_add]
    checks_at = next((k for k, ln in enumerate(block)
                      if re.match(r"^\s*checks:\s*(\[\s*\])?\s*$", ln)), None)
    if checks_at is None:
        if any(re.match(r"^\s*checks:", ln) for ln in block):
            # Some other form: a flow sequence with entries, an alias, a folded block.
            # Adding a second `checks:` key would still parse — PyYAML keeps the last
            # one — and silently drop one of the two lists.
            raise RecipeEditError(
                f"the step at {path}:{index + 1} declares `checks:` in a form this cannot "
                "extend safely; add the entries by hand")
        block = block[:1] + [f"{indent}checks:"] + rendered + block[1:]
    else:
        # `checks: []` has to lose the empty flow list first, or the block entries below
        # it become a second value for the same key.
        block[checks_at] = re.sub(r"checks:\s*\[\s*\]\s*$", "checks:", block[checks_at])
        insert_at = checks_at + 1
        while insert_at < len(block) and re.match(r"^\s*-\s", block[insert_at]):
            insert_at += 1
        block = block[:insert_at] + rendered + block[insert_at:]

    path.write_text("\n".join(lines[:index] + block + lines[end:]) + "\n", encoding="utf-8")
    return to_add


def _frontmatter_end(lines: list[str], path: pathlib.Path) -> int:
    """Index of the closing `---` of the YAML frontmatter."""
    if not lines or lines[0].strip() != "---":
        raise RecipeEditError(f"{path} does not start with YAML frontmatter")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return i
    raise RecipeEditError(f"{path} has an unterminated YAML frontmatter")


def _render(command: str, indent: str) -> str:
    quote = "'" if _needs_single_quotes(command) else '"'
    return f"{indent}  - {quote}{command}{quote}"


def _needs_single_quotes(command: str) -> bool:
    """A command containing a double quote has to be single-quoted in YAML; one that
    contains both is not something this writer will guess at."""
    if '"' in command and "'" in command:
        raise RecipeEditError(f"cannot quote a command containing both quote styles: {command}")
    return '"' in command
