from __future__ import annotations

import json
import os
import pathlib
import sys
from collections.abc import Sequence

from rig_workbench.ports import Env
from rig_workbench.ports.local import OS_ENV

from .manifest import canonical, digest
from .model import PackError, ResolvedAsset

# ── what counts as "the consent flag was actually passed" ─────────────────────
# The check used to be `"--allow-project-packs" in sys.argv`, and sys.argv carries
# far more than this process's own options: task titles, `--goal` bodies, and the
# arguments forwarded to a pack. Any of those merely *being* the literal string was
# enough to trust a project-tier asset — whose command/recipe bodies then run — so
#
#     rig-wb wb new --type bugfix -- --allow-project-packs
#     rig-wb run recipe.md --goal --allow-project-packs
#
# granted trust silently, on text the user typed as data rather than as an option.
#
# `passed_as_option` recognises the flag only where it is genuinely an option of
# this process. It deliberately does not parse the CLI; it applies the three rules
# that hold across every rig-wb command:
#
#   * argv[0] is the program name, never an option;
#   * a bare `--` ends this process's own options. Everything after it is data (a
#     positional that would otherwise be read as an option — the only way to give
#     `wb new` a task title starting with `-`) or arguments forwarded to a pack or
#     provider. The scan stops there;
#   * the token following a free-text option is that option's *value*. rig-wb's
#     hand-rolled parsers take the next token unconditionally
#     (`a == "--goal" and i + 1 < len(args)`), so `--goal --allow-project-packs`
#     really does land the flag in argv as prose somebody typed;
#   * only the exact token matches: `--allow-project-packs=anything` is a different
#     token, and this flag takes no value.
#
# What it does NOT catch, knowingly:
#
#   * a value given to a value-taking option that is not in _FREE_TEXT_OPTIONS
#     (`--out --allow-project-packs`). Ambiguity has to fail *open* here: the
#     tempting stricter rule — "refuse whenever the previous token starts with a
#     dash" — also refuses the real `rig-wb pack sync . --json --allow-project-packs`,
#     and an escape hatch that stops working is one people route around with
#     something worse. The list covers the options whose value is free prose, which
#     is where attacker-chosen text actually arrives.
#   * an argument forwarded to a pack *before* any `--`
#     (`rig-wb pack invoke p:e --allow-project-packs`, argparse.REMAINDER): that is
#     character-for-character the same command line as a genuine option, and telling
#     the two apart needs the caller's own parse, not argv. Closing it means having
#     `invoke_pack` hand this gate the argv it owns rather than the process's.
#
# Consent by environment (RIG_ALLOW_PROJECT_PACKS=1) is unaffected: env is not argv.
_CONSENT_FLAG = "--allow-project-packs"

# Options whose value is free text a user — or an agent relaying a user — typed.
_FREE_TEXT_OPTIONS = frozenset({
    "--about", "--body", "--check", "--command", "--constraints", "--evidence",
    "--goal", "--input", "--judge-command", "--note", "--producer-claim",
    "--provider-cmd", "--query", "--reason", "--slug", "--summary", "--task",
    "--title", "--with",
})


def passed_as_option(flag: str, argv: Sequence[str] | None = None) -> bool:
    """True only where `flag` is genuinely an option of this invocation, not argv text."""
    tokens = list(sys.argv if argv is None else argv)
    for index in range(1, len(tokens)):  # argv[0] is the program, never an option
        token = tokens[index]
        if token == "--":
            return False  # the option region is over; the rest is data
        if token == flag and tokens[index - 1] not in _FREE_TEXT_OPTIONS:
            return True
    return False


def _store_path(*, env: Env = OS_ENV) -> pathlib.Path:
    configured = env.get("RIG_PACK_TRUST_STORE") or env.get("RIG_TRUST_STORE")
    return pathlib.Path(configured).expanduser() if configured else pathlib.Path.home() / ".rig" / "trusted-pack-assets.json"


def _identity(asset: ResolvedAsset) -> dict:
    pack_manifest = pathlib.Path(asset.source) / "pack.yaml" if asset.pack_id else None
    return {
        "kind": asset.kind, "path": str(asset.path.resolve()),
        "content_sha256": digest(asset.path),
        "pack_sha256": digest(pack_manifest) if pack_manifest and pack_manifest.is_file() else None,
        "tier": asset.tier,
    }


def ensure_asset_trusted(asset: ResolvedAsset, *, env: Env = OS_ENV) -> pathlib.Path:
    if asset.tier not in {"project", "user", "org"}:
        return asset.path
    identity = _identity(asset)
    key = f"{asset.kind}:{identity['path']}"
    store_path = _store_path(env=env)
    try:
        store = json.loads(store_path.read_text(encoding="utf-8")) if store_path.is_file() else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        store = {}
    if store.get(key) == identity:
        return asset.path
    allowed = (
        env.get("RIG_ALLOW_PROJECT_PACKS") == "1"
        or env.get(f"RIG_ALLOW_PROJECT_{asset.kind.upper().replace('-', '_')}S") == "1"
        or passed_as_option(_CONSENT_FLAG)
    )
    if not allowed:
        raise PackError(
            f"untrusted {asset.tier} {asset.kind} asset: {asset.path}; "
            "review it and approve with RIG_ALLOW_PROJECT_PACKS=1"
        )
    store[key] = identity
    try:
        store_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = store_path.with_suffix(store_path.suffix + ".tmp")
        temporary.write_text(canonical(store), encoding="utf-8")
        os.replace(temporary, store_path)
    except OSError as exc:
        raise PackError(f"cannot persist pack trust record: {exc}") from exc
    return asset.path
