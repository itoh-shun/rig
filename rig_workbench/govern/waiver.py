"""govern.waiver — `--force` with a name, a reason and an expiry date on it.

v1's `accept --force` was honest about being an override: it warned, it set
`forced: true`, it wrote an audit line. What it could not do was distinguish
"the team lead signed off on shipping this with one criterion unmet, until
Friday" from "somebody was tired at 19:00". Both look identical in the log.

A waiver is the first: an exception someone with the authority granted,
covering named criteria, for a named reason, until a stated date. Once a policy
sets `waivers.required_for_force`, `--force` stops being available to anyone who
does not hold a matching live waiver — the escape hatch becomes a governed act
rather than a keystroke.

Three properties make it real rather than decorative:

  * **expiry** — waivers die. A permanent exception is just a weaker policy, and
    should be written as one, in the open.
  * **non-waivable criteria** — an org can put criteria beyond the reach of any
    waiver at all (`no_secret_leak` is the obvious one).
  * **grant authority** — issuing one takes the `waiver.grant` permission, and
    the policy can restrict it further to named roles.

Waivers live in `.rig/waivers.json` (project-wide, not per-task: they usually
cover a known gap that several tasks trip over) and every grant, use and
revocation goes to the ledger.
"""

from __future__ import annotations

import dataclasses
import datetime
import fnmatch
import json
import pathlib

from .policy import EffectivePolicy


def waivers_path(root: pathlib.Path) -> pathlib.Path:
    return root / ".rig" / "waivers.json"


def load_waivers(root: pathlib.Path) -> list[dict]:
    p = waivers_path(root)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = data.get("waivers", [])
    return [w for w in data if isinstance(w, dict)] if isinstance(data, list) else []


def save_waivers(root: pathlib.Path, waivers: list[dict]) -> None:
    p = waivers_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema": "rig.waivers/v2", "waivers": waivers},
                            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class WaiverError(Exception):
    """A waiver cannot be granted as asked (authority, lifetime, or scope)."""


def _today() -> datetime.date:
    return datetime.datetime.now().astimezone().date()


def _parse_date(text: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        raise WaiverError(f"'{text}' is not a date (expected YYYY-MM-DD)") from None


def grant(root: pathlib.Path, eff: EffectivePolicy, *, waiver_id: str, actor: str,
          criteria: list[str], reason: str, expires: str, scope: str = "*") -> dict:
    """Issue a waiver. Raises WaiverError when the policy does not allow it as asked.

    Authority (the `waiver.grant` permission) is checked by the caller — the CLI
    — so this function stays usable in tests and in the enforcement path without
    a second identity lookup. What it enforces here is everything the *policy
    document* says about the shape of a waiver: which criteria are beyond
    waiving, how long one may live, and which roles may issue them.
    """
    rule = eff.waivers or {}
    if not reason.strip():
        raise WaiverError("a waiver needs a reason — an unexplained exception is indistinguishable "
                          "from a mistake when it is read back in three months")
    non_waivable = set(rule.get("non_waivable") or [])
    blocked = sorted(set(criteria) & non_waivable)
    if blocked:
        raise WaiverError(
            f"criteria {', '.join(blocked)} are marked non-waivable by the org policy and cannot be "
            "covered by any waiver")
    expiry = _parse_date(expires)
    if expiry <= _today():
        raise WaiverError(f"expiry {expires} is not in the future")
    max_days = rule.get("max_days")
    if max_days:
        limit = _today() + datetime.timedelta(days=float(max_days))
        if expiry > limit:
            raise WaiverError(
                f"expiry {expires} exceeds the policy limit of {max_days:g} days "
                f"(latest allowed: {limit.isoformat()})")
    record = {
        "id": waiver_id,
        "criteria": sorted(set(criteria)),
        "scope": scope,
        "reason": reason.strip(),
        "granted_by": actor,
        "granted_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "expires": expiry.isoformat(),
        "revoked": False,
    }
    waivers = [w for w in load_waivers(root) if w.get("id") != waiver_id]
    waivers.append(record)
    save_waivers(root, waivers)
    return record


def revoke(root: pathlib.Path, waiver_id: str, *, actor: str, reason: str = "") -> dict:
    waivers = load_waivers(root)
    for w in waivers:
        if w.get("id") == waiver_id:
            w["revoked"] = True
            w["revoked_by"] = actor
            w["revoked_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
            if reason:
                w["revoked_reason"] = reason
            save_waivers(root, waivers)
            return w
    raise WaiverError(f"no waiver with id '{waiver_id}'")


def is_active(waiver: dict, *, on: datetime.date | None = None) -> bool:
    if waiver.get("revoked"):
        return False
    try:
        return datetime.date.fromisoformat(waiver.get("expires", "")) >= (on or _today())
    except ValueError:
        return False


@dataclasses.dataclass
class Coverage:
    covered: list[str]
    uncovered: list[str]
    used: list[dict]
    expired: list[dict]

    @property
    def complete(self) -> bool:
        return not self.uncovered


def coverage(root: pathlib.Path, criteria: list[str], *, task_type: str = "",
             task_id: str = "") -> Coverage:
    """Which of `criteria` a live waiver covers.

    `scope` is an fnmatch pattern tested against both the task_type and the
    task_id, so a waiver can be pinned to one migration ("rig-2026*") or opened
    to a class of work ("documentation") without inventing a query language.
    """
    waivers = load_waivers(root)
    covered: set[str] = set()
    used: list[dict] = []
    expired: list[dict] = []
    for w in waivers:
        applies = set(w.get("criteria") or []) & set(criteria)
        if not applies:
            continue
        scope = w.get("scope") or "*"
        if not (fnmatch.fnmatch(task_type or "", scope) or fnmatch.fnmatch(task_id or "", scope)):
            continue
        if not is_active(w):
            expired.append(w)
            continue
        covered |= applies
        used.append(w)
    return Coverage(covered=sorted(covered),
                    uncovered=sorted(set(criteria) - covered),
                    used=used, expired=expired)
