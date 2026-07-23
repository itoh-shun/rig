"""validation selftest: regression test of the validator itself (split from scripts/validate.py)."""

import pathlib
import sys
import traceback

from . import state
from .drill import check_drill_coverage
from .manifest import check_manifest
from .recipes import check_recipe
from .state import _emit


# ── selftest (regression test of validate.py itself; #232) ───────────────────
def run_selftest() -> None:
    """Detect implementation drift in the FAIL/WARN decision logic via synthetic fixtures.

    Same positioning as `orchestrate.py selftest` (the doctor's own doctor).
    Writes minimal recipe frontmatter to a temporary directory instead of real
    files, runs it through `check_recipe()` as-is, and verifies it does/does not
    FAIL as expected (the signature of `check_recipe` stays unchanged).
    The first stage focuses on the 4 classes that already caused real damage:
    #227 (gate enum values), #228 (boolean types; 2 representative cases),
    #219 (id slug format), and #218 (checks type / empty entries).
    """
    import tempfile

    def recipe(name: str, extra_top: str, steps_yaml: str) -> str:
        return (
            f"---\nname: {name}\ndescription: selftest fixture\nscope: project\n"
            f"autonomy: interactive\n{extra_top}steps:\n{steps_yaml}---\n\n# {name}\n"
        )

    scenarios: list[tuple[str, bool, str]] = [
        ("gate-ok", False, recipe("gate-ok", "",
            "  - id: verify\n    instruction: verify\n    gate: acceptance-gate\n")),
        ("gate-bad-serial", True, recipe("gate-bad-serial", "",
            "  - id: verify\n    instruction: verify\n    gate: serial\n")),
        ("bool-bad-capture", True, recipe("bool-bad-capture", 'capture: "yes"\n',
            "  - id: implement\n    instruction: implement\n")),
        ("bool-bad-design", True, recipe("bool-bad-design", "design: 1\n",
            "  - id: implement\n    instruction: implement\n")),
        ("id-ok", False, recipe("id-ok", "",
            "  - id: valid-step-2\n    instruction: implement\n")),
        ("id-bad-space", True, recipe("id-bad-space", "",
            '  - id: "My Step"\n    instruction: implement\n')),
        ("checks-ok", False, recipe("checks-ok", "",
            '  - id: verify\n    instruction: verify\n    checks: ["npm test"]\n')),
        ("checks-bad-scalar", True, recipe("checks-bad-scalar", "",
            '  - id: verify\n    instruction: verify\n    checks: "npm test"\n')),
        ("checks-bad-empty", True, recipe("checks-bad-empty", "",
            '  - id: verify\n    instruction: verify\n    checks: ["npm test", ""]\n')),
    ]

    # drill-coverage scenarios (#266): run through check_drill_coverage() against a
    # synthetic seed catalog and verify the WARN does/does not appear (never FAIL —
    # coverage guidance). Appended after the recipe-schema scenarios (additions only).
    drill_catalog = (
        "# instruction: drill\n\n"
        "| 種の class | 例 | 検出すべき観点 | 期待 severity | 期待 blocking |\n"
        "|---|---|---|---|---|\n"
        "| 認可漏れ | x | security | High | Blocking |\n"
        "| 過剰抽象 | x | design / lazy-senior | Low | Non-blocking |\n"
    )
    drill_scenarios: list[tuple[str, bool, str]] = [
        ("drill-covered", False, recipe("drill-covered", "",
            "  - id: review\n    instruction: parallel-review\n    gate: review-gate\n"
            "    personas: [security-reviewer, design-reviewer]\n")),
        ("drill-uncovered-reviewer", True, recipe("drill-uncovered-reviewer", "",
            "  - id: review\n    instruction: parallel-review\n    gate: review-gate\n"
            "    personas: [roast-reviewer]\n")),
        ("drill-gate-no-reviewer", True, recipe("drill-gate-no-reviewer", "",
            "  - id: verify\n    instruction: verify\n    gate: acceptance-gate\n"
            "    acceptance: [\"ok\"]\n    personas: [implementer]\n")),
    ]

    # manifest value-key scenarios (#341): a synthetic `.claude/rig.md`-shaped
    # frontmatter file run through check_manifest(), verifying the 5
    # mechanically-determinable value checks FAIL/pass as expected.
    def manifest(extra: str) -> str:
        return f"---\n{extra}---\n\n# manifest\n"

    manifest_scenarios: list[tuple[str, bool, str]] = [
        ("manifest-backend-ok", False, manifest("default_backend: workflow\n")),
        ("manifest-backend-bad", True, manifest("default_backend: manul\n")),
        ("manifest-budget-ok", False, manifest("default_budget: mid\n")),
        ("manifest-budget-bad", True, manifest("default_budget: lo\n")),
        ("manifest-orchestrate-ok", False, manifest("default_orchestrate: true\n")),
        ("manifest-orchestrate-bad-type", True, manifest('default_orchestrate: "yes"\n')),
        ("manifest-worktree-ok", False, manifest("worktree:\n  enabled: false\n")),
        ("manifest-worktree-bad-type", True, manifest('worktree:\n  enabled: "yes"\n')),
        ("manifest-size-thresholds-ok", False,
            manifest("size_thresholds:\n  S_max: 50\n  M_max: 150\n")),
        ("manifest-size-thresholds-bad-order", True,
            manifest("size_thresholds:\n  S_max: 300\n")),  # 300 >= default M_max(200)
        ("manifest-size-thresholds-bad-type", True,
            manifest("size_thresholds:\n  S_max: not-a-number\n")),
    ]

    ok = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        for stem, expect_fail, content in scenarios:
            fixture = tmp_path / f"{stem}.md"
            fixture.write_text(content, encoding="utf-8")
            start = len(state.results)
            try:
                check_recipe(fixture)
            except Exception:
                _emit("FAIL", f"selftest '{stem}' — error while running check_recipe:\n{traceback.format_exc()}")
            got_fail = any(line.startswith("[FAIL]") for line in state.results[start:])
            passed = got_fail == expect_fail
            ok += passed
            print(f"  [{'OK' if passed else 'NG'}] {stem}"
                  f" (expected: {'FAIL' if expect_fail else 'no-FAIL'} / actual: {'FAIL' if got_fail else 'no-FAIL'})")

        catalog_path = tmp_path / "drill-catalog.md"
        catalog_path.write_text(drill_catalog, encoding="utf-8")
        for stem, expect_warn, content in drill_scenarios:
            fixture = tmp_path / f"{stem}.md"
            fixture.write_text(content, encoding="utf-8")
            start = len(state.results)
            try:
                check_drill_coverage([fixture], drill_instruction=catalog_path)
            except Exception:
                _emit("FAIL", f"selftest '{stem}' — error while running check_drill_coverage:\n{traceback.format_exc()}")
            got_warn = any(line.startswith("[WARN]") for line in state.results[start:])
            got_fail = any(line.startswith("[FAIL]") for line in state.results[start:])
            passed = got_warn == expect_warn and not got_fail
            ok += passed
            print(f"  [{'OK' if passed else 'NG'}] {stem}"
                  f" (expected: {'WARN' if expect_warn else 'no-WARN'} / actual: {'WARN' if got_warn else 'no-WARN'})")

        for stem, expect_fail, content in manifest_scenarios:
            fixture = tmp_path / f"{stem}.md"
            fixture.write_text(content, encoding="utf-8")
            start = len(state.results)
            try:
                check_manifest(fixture)
            except Exception:
                _emit("FAIL", f"selftest '{stem}' — error while running check_manifest:\n{traceback.format_exc()}")
            got_fail = any(line.startswith("[FAIL]") for line in state.results[start:])
            passed = got_fail == expect_fail
            ok += passed
            print(f"  [{'OK' if passed else 'NG'}] {stem}"
                  f" (expected: {'FAIL' if expect_fail else 'no-FAIL'} / actual: {'FAIL' if got_fail else 'no-FAIL'})")

    total = len(scenarios) + len(drill_scenarios) + len(manifest_scenarios)
    print(f"\nselftest: {ok}/{total} scenarios OK")
    sys.exit(0 if ok == total else 1)
