"""rig govern — the organisational layer of the AI Quality Operating System (v2.0.0).

v1 assumed one person and one repository: `.rig/gates.json` extended the gate,
`.rig/access.json` held an accept allowlist, `.rig/audit.jsonl` recorded forced
accepts. All of it worked, and none of it composed across teams — every project
was its own island, so "what is the shared bar, and did team B actually clear
it?" had no answer.

v2 keeps every one of those files working and adds the layer above them:

    team A ─┐
    team B ─┼─→ common policy ─→ permissions → approvals → waivers → audit
    team C ─┘                              (the governed accept)

Six first-class concepts, each a module here:

  policy      a versioned document that only ever *tightens* downstream
              (org → team → project). A child layer may add criteria, raise a
              quorum, shorten a waiver; it can never remove or loosen one.
  identity    who is acting, and which org/team this repository belongs to.
  rbac        roles → permissions, resolved through the policy layers.
  approval    approval requests/decisions with quorum and separation of duties.
  waiver      time-boxed, owned, audited exceptions — `--force` with a name on it.
  ledger      hash-chained, tamper-evident audit trail over all of the above.
  conformance measures a project against its effective policy, and rolls several
              projects up into the org view.

**Inert by default.** With no `.rig/org.json` and no policy layers, every entry
point here reports "governance not configured" and changes nothing — solo use of
rig behaves exactly as it did in v1. Governance is something a team switches on,
not a tax the single developer pays.
"""

from .policy import (PERMISSIONS, PolicyError, EffectivePolicy, effective_policy,
                     load_policy_document, describe_layers)
from .identity import current_actor, load_org_binding, OrgBinding
from .rbac import can, require, roles_of, explain

__all__ = [
    "PERMISSIONS",
    "PolicyError",
    "EffectivePolicy",
    "effective_policy",
    "load_policy_document",
    "describe_layers",
    "current_actor",
    "load_org_binding",
    "OrgBinding",
    "can",
    "require",
    "roles_of",
    "explain",
]
