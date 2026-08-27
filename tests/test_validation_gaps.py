"""Checks that validate.md specified but the validator did not perform.

Each of these was a documented rule with no executable counterpart, so the thing
under test is not "does the check work" but "does the check exist at all" — a
recipe or catalog entry that violates the spec must now be reported, where
before it passed in silence.

  #362  step model / verifier_model type
  #358  step auto_route.candidates schema + cheapest-first order
  #364  catalog drift scan covers patterns/
  #365  accumulated/ frontmatter and required sections
  #188  did-you-mean for an unresolvable recipe name
"""

import pathlib

import pytest

from rig_workbench.orchestrate.recipes import suggest_recipe_names
from rig_workbench.validation import state as validation_state
from rig_workbench.validation.accumulated import check_accumulated
from rig_workbench.validation.recipes import _check_auto_route, _check_model_field

CTX = "recipe demo step implement"


@pytest.fixture(autouse=True)
def _reset_validation_state():
    validation_state.results.clear()
    validation_state._pass = validation_state._warn = validation_state._fail = 0
    yield
    validation_state.results.clear()
    validation_state._pass = validation_state._warn = validation_state._fail = 0


# ── model / verifier_model (#362) ───────────────────────────────────────


@pytest.mark.parametrize("field", ["model", "verifier_model"])
def test_a_string_model_is_accepted(field):
    _check_model_field({field: "claude-opus-5"}, CTX)
    assert validation_state.results == []


@pytest.mark.parametrize("value", [123, ["claude-opus-5"], True, {"name": "x"}])
def test_a_non_string_model_fails_before_it_reaches_argv(value):
    _check_model_field({"model": value}, CTX)
    assert validation_state._fail == 1


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_model_warns_because_it_silently_reverts_to_the_default(value):
    """Empty is falsy, so the provider uses its default and the recipe's
    explicit choice disappears with nothing printed."""
    _check_model_field({"verifier_model": value}, CTX)
    assert validation_state._warn == 1
    assert validation_state._fail == 0


def test_an_absent_model_is_not_checked():
    _check_model_field({"instruction": "implement"}, CTX)
    assert validation_state.results == []


# ── auto_route.candidates (#358) ────────────────────────────────────────


def _candidates(*entries):
    return {"candidates": list(entries)}


def _c(model, size, tier="cheap"):
    return {"model": model, "cost_tier": tier, "max_size": size}


def test_cheapest_first_candidates_are_accepted():
    _check_auto_route(_candidates(_c("a", "S"), _c("b", "M"), _c("c", "XL")), CTX)
    assert validation_state.results == []


def test_out_of_order_candidates_fail():
    """Selection takes the first candidate large enough, so declaring the
    expensive tier first makes it win every route."""
    _check_auto_route(_candidates(_c("expensive", "XL"), _c("cheap", "S")), CTX)
    assert validation_state._fail == 1
    assert "cheapest-first" in validation_state.results[0]


def test_equal_max_sizes_are_not_treated_as_out_of_order():
    _check_auto_route(_candidates(_c("a", "M"), _c("b", "M")), CTX)
    assert validation_state.results == []


@pytest.mark.parametrize("bad", [
    {"cost_tier": "cheap", "max_size": "S"},                     # no model
    {"model": "a", "max_size": "S"},                             # no cost_tier
    {"model": "", "cost_tier": "cheap", "max_size": "S"},        # empty model
    {"model": "a", "cost_tier": "cheap"},                        # no max_size
    {"model": "a", "cost_tier": "cheap", "max_size": "HUGE"},    # unknown size
])
def test_a_malformed_candidate_fails(bad):
    _check_auto_route(_candidates(bad), CTX)
    assert validation_state._fail == 1


def test_an_unknown_max_size_says_why_it_matters():
    _check_auto_route(_candidates(_c("a", "medium")), CTX)
    assert "XL" in validation_state.results[0]


@pytest.mark.parametrize("value", [{"candidates": []}, {"candidates": "bugfix"}, {}, "auto"])
def test_auto_route_without_usable_candidates_fails(value):
    _check_auto_route(value, CTX)
    assert validation_state._fail == 1


def test_an_absent_auto_route_is_not_checked():
    _check_auto_route(None, CTX)
    assert validation_state.results == []


# ── catalog drift covers patterns/ (#364) ───────────────────────────────


def test_every_shipped_pattern_is_listed_in_the_skill_catalog():
    """The regression #364 describes: patterns/failure-taxonomy was wired in,
    used, and tested, yet absent from §2 with nothing able to notice."""
    root = pathlib.Path(__file__).resolve().parent.parent
    skill = (root / "skills/engine/SKILL.md").read_text(encoding="utf-8")
    patterns = sorted(root.glob("skills/engine/patterns/*.md"))
    assert patterns, "no shipped patterns found"
    missing = [p.stem for p in patterns if p.stem.startswith("_") is False and p.stem not in skill]
    assert missing == []


def _catalog_tree(tmp_path, monkeypatch):
    from rig_workbench.validation import catalog

    skills = tmp_path / "skills" / "engine"
    facets = skills / "facets"
    for directory in (
        skills / "recipes", skills / "patterns", facets / "instructions",
        facets / "personas", facets / "output-contracts", facets / "policies",
        facets / "knowledge" / "wiki",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (skills / "SKILL.md").write_text(
        "## 2. ブリック目録\n\n## 3. PARSE\n", encoding="utf-8"
    )
    monkeypatch.setattr(catalog, "SKILLS", skills)
    monkeypatch.setattr(catalog, "FACETS", facets)
    return catalog, skills


@pytest.mark.parametrize("relative", [
    "patterns/unlisted.md",
    "facets/output-contracts/unlisted.md",
    "facets/policies/unlisted.md",
    "facets/knowledge/unlisted.md",
])
def test_catalog_drift_warns_for_an_unlisted_brick(relative, tmp_path, monkeypatch):
    catalog, skills = _catalog_tree(tmp_path, monkeypatch)
    (skills / relative).write_text("unlisted", encoding="utf-8")

    catalog.check_catalog_drift()

    assert validation_state._warn == 1
    assert relative in validation_state.results[0]


@pytest.mark.parametrize("relative", [
    "patterns/listed.md",
    "facets/output-contracts/listed.md",
    "facets/policies/listed.md",
    "facets/knowledge/listed.md",
])
def test_catalog_drift_accepts_a_listed_brick(relative, tmp_path, monkeypatch):
    catalog, skills = _catalog_tree(tmp_path, monkeypatch)
    (skills / relative).write_text("listed", encoding="utf-8")
    (skills / "SKILL.md").write_text(
        f"## 2. ブリック目録\n`{relative.removesuffix('.md')}`\n\n## 3. PARSE\n",
        encoding="utf-8",
    )

    catalog.check_catalog_drift()

    assert validation_state._warn == 0
    assert validation_state._fail == 0


def test_catalog_drift_derives_new_facet_collections(tmp_path, monkeypatch):
    """A fourth omitted facet kind must become covered without editing a tuple."""
    catalog, skills = _catalog_tree(tmp_path, monkeypatch)
    future = skills / "facets" / "future-contracts" / "unlisted.md"
    future.parent.mkdir()
    future.write_text("unlisted", encoding="utf-8")

    catalog.check_catalog_drift()

    assert validation_state._warn == 1
    assert "facets/future-contracts/unlisted.md" in validation_state.results[0]


@pytest.mark.parametrize("relative", [
    "facets/knowledge/wiki/unlisted.md",
    "facets/knowledge/_schema.md",
])
def test_catalog_drift_accepts_files_owned_by_another_rule_or_private(
    relative, tmp_path, monkeypatch
):
    catalog, skills = _catalog_tree(tmp_path, monkeypatch)
    (skills / relative).write_text("not a §2 listing", encoding="utf-8")

    catalog.check_catalog_drift()

    assert validation_state._warn == 0


# ── accumulated/ schema (#365) ──────────────────────────────────────────


GOOD_BODY = "\n## 何が起きたか\n\nx\n\n## 次回への示唆\n\ny\n"


def _accumulated(tmp_path: pathlib.Path, name: str, frontmatter: str, body: str = GOOD_BODY):
    directory = tmp_path / "accumulated"
    directory.mkdir(exist_ok=True)
    (directory / name).write_text(f"---\n{frontmatter}---\n{body}", encoding="utf-8")
    return directory


def test_a_missing_directory_says_nothing(tmp_path):
    check_accumulated(tmp_path / "nope")
    assert validation_state.results == []


def test_a_well_formed_entry_warns_about_nothing(tmp_path):
    directory = _accumulated(
        tmp_path, "pitfall-jwt.md",
        'category: pitfall\ntitle: "JWT の失効"\ndate: 2026-06-10\n',
    )
    check_accumulated(directory)
    assert validation_state._warn == 0
    assert validation_state._pass == 1


def test_an_unquoted_date_is_accepted_as_the_date_yaml_makes_it(tmp_path):
    """PyYAML turns 2026-06-10 into a date object; that is the spec's format,
    not a violation of it."""
    directory = _accumulated(
        tmp_path, "a.md", "category: decision\ntitle: x\ndate: 2026-06-10\n"
    )
    check_accumulated(directory)
    assert validation_state._warn == 0


@pytest.mark.parametrize("frontmatter,needle", [
    ('category: unknwon\ntitle: x\ndate: 2026-06-10\n', "category"),
    ('category: pitfall\ntitle: ""\ndate: 2026-06-10\n', "title"),
    ('category: pitfall\ntitle: x\ndate: "2026/06/10"\n', "date"),
])
def test_each_frontmatter_violation_warns_once(tmp_path, frontmatter, needle):
    directory = _accumulated(tmp_path, "a.md", frontmatter)
    check_accumulated(directory)
    assert validation_state._warn == 1
    assert needle in validation_state.results[0]


@pytest.mark.parametrize("body,missing", [
    ("\n## 次回への示唆\n\ny\n", "何が起きたか"),
    ("\n## 何が起きたか\n\nx\n", "次回への示唆"),
])
def test_a_missing_required_section_warns(tmp_path, body, missing):
    directory = _accumulated(
        tmp_path, "a.md", "category: pitfall\ntitle: x\ndate: 2026-06-10\n", body=body
    )
    check_accumulated(directory)
    assert validation_state._warn == 1
    assert missing in validation_state.results[0]


def test_a_section_named_inside_prose_does_not_count_as_present(tmp_path):
    directory = _accumulated(
        tmp_path, "a.md", "category: pitfall\ntitle: x\ndate: 2026-06-10\n",
        body="\n本文で ## 何が起きたか に触れただけ\n\n## 次回への示唆\n\ny\n",
    )
    check_accumulated(directory)
    assert validation_state._warn == 1


def test_accumulated_never_fails_only_warns(tmp_path):
    """A malformed knowledge file degrades context; it does not stop a run."""
    directory = _accumulated(tmp_path, "a.md", "category: nope\ntitle: ''\n", body="\n")
    check_accumulated(directory)
    assert validation_state._fail == 0
    assert validation_state._warn >= 3


# ── did-you-mean for recipe names (#188) ────────────────────────────────


@pytest.fixture
def recipe_dirs(tmp_path):
    project = tmp_path / "project"
    shipped = tmp_path / "shipped"
    for directory in (project, shipped):
        directory.mkdir()
    for name in ("hotfix", "release-flow", "review-only", "pr-review", "bugfix", "_template"):
        (shipped / f"{name}.md").write_text("x", encoding="utf-8")
    return project, shipped


@pytest.mark.parametrize("typo,expected", [
    ("hotfixx", "hotfix"),
    ("release_flow", "release-flow"),
    ("bugifx", "bugfix"),
    ("Hotfix", "hotfix"),
])
def test_a_near_miss_is_suggested(recipe_dirs, typo, expected):
    suggestions = suggest_recipe_names(typo, list(recipe_dirs))
    assert suggestions[0][0] == expected


def test_an_abbreviation_is_suggested_shortest_overshoot_first(recipe_dirs):
    """`review` is far in edit distance from `review-only` but is plainly what
    the caller meant."""
    names = [stem for stem, _tier in suggest_recipe_names("review", list(recipe_dirs))]
    assert names[:2] == ["pr-review", "review-only"]


def test_nothing_close_produces_no_suggestions(recipe_dirs):
    assert suggest_recipe_names("xyz123", list(recipe_dirs)) == []


def test_suggestions_are_capped_at_three(recipe_dirs):
    project, shipped = recipe_dirs
    for name in ("aaa", "aab", "aac", "aad", "aae"):
        (shipped / f"{name}.md").write_text("x", encoding="utf-8")
    assert len(suggest_recipe_names("aaa", [project, shipped])) == 3


def test_the_tier_shown_is_the_directory_the_name_came_from(recipe_dirs):
    project, shipped = recipe_dirs
    (project / "hotfox.md").write_text("x", encoding="utf-8")
    tiers = dict(suggest_recipe_names("hotfix", [project, shipped]))
    assert tiers["hotfox"] == "project"
    assert tiers["hotfix"] == "shipped"


def test_the_project_tier_wins_a_tie_because_it_wins_resolution(recipe_dirs):
    project, shipped = recipe_dirs
    (project / "hotfix.md").write_text("x", encoding="utf-8")
    suggestions = suggest_recipe_names("hotfixx", [project, shipped])
    assert suggestions[0] == ("hotfix", "project")


def test_an_org_tier_between_project_and_shipped_is_labelled_org(recipe_dirs, tmp_path):
    project, shipped = recipe_dirs
    org = tmp_path / "org"
    org.mkdir()
    (org / "team-flow.md").write_text("x", encoding="utf-8")
    tiers = dict(suggest_recipe_names("team-flow", [project, org, shipped]))
    assert tiers["team-flow"] == "org"


def test_underscore_prefixed_templates_are_not_suggested(recipe_dirs):
    names = [stem for stem, _tier in suggest_recipe_names("_templat", list(recipe_dirs))]
    assert "_template" not in names
