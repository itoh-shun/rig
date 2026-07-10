"""validation release: plugin.json ⇄ CHANGELOG / skills-lock.json checks (split from scripts/validate.py)."""

import json
import re

from .config import ROOT
from .state import _emit


# ── release metadata consistency (plugin.json ⇄ CHANGELOG.md; #231) ──────────
def check_release_metadata() -> None:
    """Check that CHANGELOG.md has a `## [x.y.z]` section matching plugin.json's version.

    release.yml silently falls back to auto-generated notes when this match is
    not found (by design it does not block the release itself). The --validate
    side detects it as FAIL to prevent it from slipping in unnoticed.
    """
    plugin_path = ROOT / ".claude-plugin" / "plugin.json"
    changelog_path = ROOT / "CHANGELOG.md"
    if not plugin_path.is_file() or not changelog_path.is_file():
        return
    try:
        version = json.loads(plugin_path.read_text(encoding="utf-8"))["version"]
    except Exception as exc:
        _emit("FAIL", f"release — cannot read version from .claude-plugin/plugin.json: {exc}")
        return
    changelog = changelog_path.read_text(encoding="utf-8")
    heading = f"## [{version}]"
    if heading not in changelog:
        _emit(
            "FAIL",
            f"release — CHANGELOG.md has no \"{heading}\" section matching"
            f" the plugin.json version ({version})",
        )
    else:
        _emit("PASS", f"release: plugin.json version ({version}) ⇄ CHANGELOG.md section match")

    # plugin.json is the release workflow's source of truth, but the pip package
    # carries its own two copies of the version — keep all three in lockstep.
    others = {
        "pyproject.toml": re.search(
            r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
            re.MULTILINE),
        "rig_workbench/__init__.py": re.search(
            r'^__version__\s*=\s*"([^"]+)"',
            (ROOT / "rig_workbench" / "__init__.py").read_text(encoding="utf-8"), re.MULTILINE),
    }
    for label, m in others.items():
        if m is None:
            _emit("FAIL", f"release — no version field found in {label}")
        elif m.group(1) != version:
            _emit("FAIL", f"release — {label} version ({m.group(1)}) != plugin.json ({version})")
        else:
            _emit("PASS", f"release: plugin.json version ({version}) ⇄ {label} match")


# ── skills-lock.json consistency (/rig:import provenance record; #249) ───────
_VALID_IMPORT_MODES = ("delegate", "translate", "knowledge")


def check_skills_lock() -> None:
    """Check the schema and importedAs reference consistency of skills-lock.json.

    Silently skips when the file does not exist (same policy as the
    wiki/accumulated checks). The first stage only targets the project layer
    (directly under the calling repository).
    """
    lock_path = ROOT / "skills-lock.json"
    if not lock_path.is_file():
        return
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception as exc:
        _emit("FAIL", f"skills-lock — cannot be parsed as JSON: {exc}")
        return
    if not isinstance(data, dict) or "version" not in data or "skills" not in data:
        _emit("FAIL", "skills-lock — top level must have version / skills keys")
        return

    skills = data["skills"]
    entries = skills.items() if isinstance(skills, dict) else enumerate(skills or [])
    ok = 0
    for key, entry in entries:
        ctx = f"skills-lock[{key}]"
        if not isinstance(entry, dict):
            _emit("FAIL", f"{ctx} — entry is not a dict")
            continue
        bad = False
        for field in ("source", "sourceType", "skillPath", "computedHash"):
            if not entry.get(field):
                _emit("FAIL", f"{ctx} — required field `{field}` is missing")
                bad = True
        mode = entry.get("mode")
        if mode is not None and mode not in _VALID_IMPORT_MODES:
            _emit("FAIL", f"{ctx} — mode '{mode}' is an invalid value. Allowed values: {', '.join(_VALID_IMPORT_MODES)}")
            bad = True
        imported_as = entry.get("importedAs")
        if imported_as is None:
            _emit("WARN", f"{ctx} — importedAs is not recorded (missing traceability of which bricks it was translated into)")
        else:
            for p in (imported_as if isinstance(imported_as, list) else [imported_as]):
                if not (ROOT / str(p)).exists():
                    _emit("FAIL", f"{ctx} — importedAs '{p}' does not exist in the repository")
                    bad = True
        if not bad:
            ok += 1
    _emit("PASS", f"skills-lock: {ok}/{len(skills)} schema OK")
