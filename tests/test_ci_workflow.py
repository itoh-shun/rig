"""Guards on the repository's own CI definition.

These are drift tests, not style checks: each one encodes a failure that already
happened once and would otherwise fail silently — a gate that cannot run, or a
check that quietly stops being wired.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/validate.yml"


def _workflow() -> dict:
    # Imported, not `importorskip`-ed. pyyaml is a *declared* dependency, so it is absent
    # only when the install is broken — and a broken install must fail here rather than turn
    # this file into a row of skips. That is not hypothetical: a `cryptography` whose
    # `_cffi_backend` was missing once turned eleven checks into skips, and a skip is the one
    # result nobody reads.
    import yaml

    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _run_steps(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_prompt_gate_installs_declared_dependencies():
    """`--no-deps` skips pyyaml, which eval needs to read recipe frontmatter.

    The job is meant to exclude *optional* extras. `pip install -e .` does that;
    `--no-deps` also drops the declared ones, and the gate then fails before it
    evaluates anything — with its error on stdout, which the workflow redirects
    to a file, so the console shows an exit code and nothing else.
    """
    steps = _run_steps(_workflow()["jobs"]["prompt-evaluation"])
    assert "pip install -e ." in steps
    assert "--no-deps" not in steps


def test_validate_job_verifies_the_coverage_map():
    """The coverage map claims evidence; unverified, it drifts back into prose."""
    steps = _run_steps(_workflow()["jobs"]["validate"])
    assert "rig_workbench.cli coverage" in steps


def test_validate_job_verifies_the_asvs_map():
    """Same reason as the coverage map: a mapping whose references rot is a false claim."""
    steps = _run_steps(_workflow()["jobs"]["validate"])
    assert "rig_workbench.cli asvs --check" in steps


def test_validate_job_keeps_the_deterministic_checks_wired():
    steps = _run_steps(_workflow()["jobs"]["validate"])
    for expected in ("ruff check", "scripts/validate.py", "orchestrate.py selftest", "pytest -q"):
        assert expected in steps, expected
