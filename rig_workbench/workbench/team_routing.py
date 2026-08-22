"""Which team to use for this kind of change, without optimisation weakening the boundary
(#438).

Fixing one provider for every role wastes money on wording changes and underspends on
authentication boundaries, and this repository already holds what it would take to choose
better — drill scores, workflow history, observed finding yield. But a router that may also
decide which constraints apply has been handed the question it was supposed to be constrained
by, which is the shape `synthesis.py` refuses for workflow steps and this module refuses for
role assignments.

**It does not choose.** Deciding that a provider is the right one for an authentication review
is reading evidence, weighing it and concluding — an agent's work, and a module that called a
model to do it would leave nothing a gate could check and nothing a mutation could falsify. An
assignment arrives as a record of what was chosen and why, and what lives here is the schema,
the constraints, and the refusals.

**Hard constraints outrank optimisation, and the constraint set is not the record's to state.**
Independence, capability, policy-approved providers, roles a change requires: a routing decision
that breaks one is refused rather than scored against cost. The constraints are built by the caller from
the policy and the assurance target, for the reason `synthesis.py` builds its floor that way —
a constraint read out of the thing being checked is not a constraint.

**An unmeasured provider is not a good one, and the router does not get to say it is measured.**
The tempting rendering of "we have no data" is a blank, a zero, or a default, and all three read
as "fine" next to a measured competitor — so a selection states how well it is known
(`measured`, `shadow`, `unmeasured`) and that statement is *reported*, not believed. Which
providers count as measured for an assurance role is `Constraints.measured`, stated by the
policy alongside every other constraint, because a record that could assert the fact unlocking
its own eligibility is the pattern this module rejects everywhere else. A role nobody is listed
as measured for admits nobody, for the reason an empty allowlist names nobody.

Shadow evaluation is what you do to a provider *before* trusting it with a verdict; promoting it
is somebody's decision, recorded in the policy, rather than a word in a routing record.

**A verifier that is the developer is not a verifier.** The same provider on both sides of a
role that requires independence produces a verdict about its own work, and no amount of
evidence about how good that provider is makes that verdict independent.

**And two names for one backend are one backend.** Comparing the strings a router wrote would
let `vendor/model-x` review what `vendor-alias/model-x` implemented, which is the same model
grading its own work under a second name. So the policy states the canonical identity of every
provider it approves, and independence is compared on that. Stating `{}` is how a policy says
its provider names are already canonical — it has to be said, because silence would mean the
check ran on whatever the router felt like calling things.

**And a policy that was not supplied is not a policy that permits everything.** Every constraint
here is the caller's to state, so the constraints document is required rather than defaulted:
a caller who forgot it, or whose policy failed to resolve, gets an error instead of an
admission.
"""

from __future__ import annotations

import dataclasses

SCHEMA = "rig.team-routing/v1"

#: The roles a change can be routed to. Closed, because a role nobody defined would be
#: accepted, dropped, and leave a reader believing it was assigned.
PLANNER = "planner"
DEVELOPER = "developer"
SECURITY_VERIFIER = "security-verifier"
ARCHITECTURE_VERIFIER = "architecture-verifier"
JUDGE = "judge"
ROLES = (PLANNER, DEVELOPER, SECURITY_VERIFIER, ARCHITECTURE_VERIFIER, JUDGE)

#: The roles whose whole value is being something other than the thing they judge. Named here
#: so `independent` is a property of the role rather than a flag a record can set for itself.
ASSURANCE_ROLES = frozenset({SECURITY_VERIFIER, ARCHITECTURE_VERIFIER, JUDGE})

#: How well this repository knows the selected provider *for this kind of work*. Ordered from
#: what can be relied on to what cannot.
MEASURED = "measured"
SHADOW = "shadow"
UNMEASURED = "unmeasured"
CONFIDENCE = (MEASURED, SHADOW, UNMEASURED)


def assignment_problems(role, provider, confidence, evidence_count, reasons,
                        where: str = "assignment") -> list[str]:
    """Everything wrong with one assignment's fields, wherever it came from.

    One function rather than a rule in `validate` and a hope on the programmatic path. Four
    review rounds found the same defect in four places, each time because the JSON path was
    checked and the object was not; a shared rule is the change that stops the fifth rather
    than the change that fixes the fourth.
    """
    problems: list[str] = []
    if role not in ROLES:
        problems.append(f"{where}: role {role!r} is not one of {', '.join(ROLES)}")
    if not _is_provider(provider):
        problems.append(
            f"{where}: provider {provider!r} has to name something, exactly. Two spellings of "
            f"one provider are two providers to a comparison and one to everything else")
    if confidence not in CONFIDENCE:
        problems.append(
            f"{where}: confidence {confidence!r} is not one of {', '.join(CONFIDENCE)}. "
            f"Leaving it out would be the gap that reads as 'fine'")
    # `type(...) is int` because `True` is an `int` and would record one observation while
    # reading as a flag somebody set.
    if type(evidence_count) is not int or evidence_count < 0:
        problems.append(
            f"{where}: evidence_count is {evidence_count!r}. The interesting value is zero, so "
            f"it is required rather than optional")
    elif evidence_count == 0 and confidence in (MEASURED, SHADOW):
        problems.append(
            f"{where}: claims to be {confidence!r} on no observations. Whatever {MEASURED!r} "
            f"and {SHADOW!r} mean, both mean something was observed — and {UNMEASURED!r} is "
            f"the word for the other case")
    if not isinstance(reasons, (list, tuple)) or not all(
            isinstance(r, str) and r.strip() for r in reasons):
        problems.append(f"{where}: reasons must be a list of non-empty strings")
    elif not reasons:
        problems.append(
            f"{where}: gives no reason. A selection nobody has to justify is a default wearing "
            f"the shape of a decision")
    return problems


@dataclasses.dataclass(frozen=True)
class Assignment:
    """One role, who was given it, and what that choice rests on.

    `reasons` is prose a human reads. `evidence_count` is the number of observations behind
    the choice, and it is required rather than optional because the interesting value is zero:
    "chosen on nothing" and "chosen on four hundred runs" are the same sentence without it.

    `confidence` is not derivable from `evidence_count` here — how many observations make a
    provider *measured* for a kind of work is a judgement about the observations, which this
    module does not have. So it is stated, and the two are checked against each other only
    where one of them cannot be true: nothing observed is not `measured`.
    """

    role: str
    provider: str
    confidence: str
    evidence_count: int
    reasons: tuple = ()

    def __post_init__(self) -> None:
        problems = assignment_problems(self.role, self.provider, self.confidence,
                                       self.evidence_count, self.reasons)
        if problems:
            raise ValueError("\n  ".join(problems))
        object.__setattr__(self, "reasons", tuple(self.reasons))

    def as_dict(self) -> dict:
        return {"role": self.role, "provider": self.provider, "confidence": self.confidence,
                "evidence_count": self.evidence_count, "reasons": list(self.reasons)}


#: The keys an assignment may carry, derived so the set `validate` accepts, the one `load`
#: reads and the ones compared cannot drift apart.
ASSIGNMENT_FIELDS = frozenset(f.name for f in dataclasses.fields(Assignment))

#: The keys the document itself may carry. `strategy` is the version of whatever decided:
#: without it a change in routing behaviour and a change in the evidence look the same
#: afterwards, and neither can be attributed.
DOCUMENT_KEYS = frozenset({"schema", "task", "strategy", "assignments"})


def _is_provider(value: object) -> bool:
    """A provider name: a non-blank string that is exactly itself once trimmed.

    The trimming rule is not cosmetic. Independence compares providers by string, so
    `"acme/m"` and `" acme/m "` would be two providers to the comparison and one to everything
    downstream that trims.
    """
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


@dataclasses.dataclass(frozen=True)
class Constraints:
    """What optimisation may not trade away, built by the caller from the policy.

    Not read out of the routing record, for the reason `synthesis.py` does not read its floor
    out of the proposal: a constraint the thing being checked gets to state is not a
    constraint. `approved` is the set of providers a policy allows at all; `capable` maps a
    role to the providers that can actually do it; `independent` names the roles that may not
    be taken by whoever wrote the change.
    """

    #: `None` is "this policy states no allowlist", which is not the same as an allowlist
    #: that names nobody. Reading an empty set as "everything is allowed" is fail-open in the
    #: exact case a caller most needs to fail closed: a policy that resolved to nothing.
    approved: frozenset | None = None
    capable: dict = dataclasses.field(default_factory=dict)
    #: Roles a policy wants independent *in addition to* the ones whose whole value is being
    #: other than what they judge. Additive for the reason `build_acceptance` is: a setting
    #: that could shrink this would let a routing document's own policy file turn the
    #: developer into the judge.
    also_independent: frozenset = frozenset()
    #: Role to the providers this policy has measured *for that role*. Assurance roles are
    #: filled from here and nowhere else: a record that could assert the fact unlocking its own
    #: eligibility would be stating its own constraint.
    measured: dict = dataclasses.field(default_factory=dict)
    #: Provider name to the backend it actually is. `{}` says the names are already canonical,
    #: and it is stated rather than assumed: silence would mean independence was compared on
    #: whatever the router felt like calling things.
    identity: dict = dataclasses.field(default_factory=dict)
    #: Roles this change cannot go without. Held here rather than passed alongside, so there
    #: is one validated object and not a second, unchecked way in.
    required: frozenset = frozenset()
    #: The task these constraints were chosen for. Stated by whoever chose them, and compared
    #: against what the record says it routed: a router free to label an authentication change
    #: as a wording change would be picking which constraints apply to it.
    task: str | None = None

    def __post_init__(self) -> None:
        # Copied and frozen before anything is validated, so what was checked is what will be
        # compared: `frozen=True` stops the fields being replaced and not the sets and dicts
        # behind them from being emptied afterwards.
        import collections.abc
        import types
        for name in ("capable", "identity", "measured"):
            # Before `dict(...)`, which turns a list of pairs into a mapping nobody wrote and
            # `[]` into "no policy at all" — the coercion this module refuses everywhere else.
            if not isinstance(getattr(self, name), collections.abc.Mapping):
                raise ValueError(
                    f"constraints: {name} is {getattr(self, name)!r}, not a mapping. Anything "
                    f"that iterates into pairs would otherwise become a policy nobody wrote")
        for name in ("capable", "measured"):
            object.__setattr__(self, name, types.MappingProxyType({
                role: frozenset(providers)
                if isinstance(providers, (frozenset, set, list, tuple)) else providers
                for role, providers in dict(getattr(self, name)).items()}))
        object.__setattr__(self, "identity", types.MappingProxyType(dict(self.identity)))
        for name in ("also_independent", "required"):
            value = getattr(self, name)
            if isinstance(value, (frozenset, set)):
                object.__setattr__(self, name, frozenset(value))
        if isinstance(self.approved, (frozenset, set)):
            object.__setattr__(self, "approved", frozenset(self.approved))

        if self.task is not None and not (isinstance(self.task, str) and self.task.strip()):
            raise ValueError(
                f"constraints: task is {self.task!r}; either name the task these were chosen "
                f"for, or leave it out")
        for name, value in (("also_independent", self.also_independent),
                            ("required", self.required)):
            # A bare string iterates as characters and a dict as its keys, so either would
            # become a role set nobody wrote — the same reinterpretation the JSON path refuses.
            if not isinstance(value, (frozenset, set)):
                raise ValueError(
                    f"constraints: {name} is {value!r}, not a set of roles. Anything that "
                    f"iterates would otherwise become roles nobody named")
        if self.approved is not None:
            if not isinstance(self.approved, (frozenset, set)):
                raise ValueError(
                    f"constraints: approved is {self.approved!r}, not a set of providers. "
                    f"Anything that iterates would otherwise approve whatever it yields")
            bad = sorted(repr(item) for item in self.approved if not _is_provider(item))
            if bad:
                raise ValueError(
                    f"constraints: approved contains {', '.join(bad)}, which is not a "
                    f"provider name")
        unknown = sorted(set(self.capable) - set(ROLES)) + \
            sorted(set(self.measured) - set(ROLES)) + \
            sorted(set(self.also_independent) - set(ROLES)) + \
            sorted(set(self.required) - set(ROLES))
        if unknown:
            raise ValueError(
                f"constraints name role(s) that do not exist: {', '.join(unknown)}. A "
                f"constraint on a role nothing can be assigned to constrains nothing")
        # `capable={JUDGE: None}` would otherwise be an authored capability rule that
        # `violations` treats exactly like an absent one — malformed policy collapsing into
        # non-enforcement, which is the whole failure mode this module is about.
        for name in ("capable", "measured"):
            for role, providers in getattr(self, name).items():
                if not isinstance(providers, (frozenset, set)):
                    raise ValueError(
                        f"constraints: {name}[{role!r}] is {providers!r}, not a set of "
                        f"providers. An unreadable rule would be treated as no rule")
                bad = sorted(repr(item) for item in providers if not _is_provider(item))
                if bad:
                    raise ValueError(
                        f"constraints: {name}[{role!r}] contains {', '.join(bad)}, which is "
                        f"not a provider name")
        for name, backend in self.identity.items():
            # `_is_provider` on both sides, not merely non-blank: independence compares the
            # canonical strings, so `" backend-7"` and `"backend-7"` would be two backends
            # here and one to anything downstream that trims — the same laundering the
            # provider names were already protected from.
            if not (_is_provider(name) and _is_provider(backend)):
                raise ValueError(
                    f"constraints: identity maps {name!r} to {backend!r}; both have to name "
                    f"something, exactly")
            # Terminal, or the mapping means two things at once. With `dev-alias → model`,
            # `judge-alias → backend` and `model → backend`, one hop makes `model` and
            # `backend` look like different backends while the policy itself says they are
            # the same one — and a router that can read the policy can pick that pair.
            if self.identity.get(backend, backend) != backend:
                raise ValueError(
                    f"constraints: identity maps {name!r} to {backend!r}, which is itself "
                    f"mapped to {self.identity[backend]!r}. A backend has to be what a name "
                    f"resolves to, not another name")

    def canonical(self, provider: str) -> str | None:
        """What this provider actually is, or `None` if the policy cannot say.

        `None` rather than the provider's own name, because "the policy did not mention it" and
        "the policy says it is itself" are the difference between a check that ran and one that
        did not.
        """
        if not self.identity:
            return provider
        return self.identity.get(provider)

    @property
    def independent(self) -> frozenset:
        """Every role that may not be taken by whoever wrote the change.

        Derived rather than stored, so there is no field a caller can set to a smaller value
        than the floor.
        """
        return ASSURANCE_ROLES | self.also_independent


def validate(payload: dict) -> list[str]:
    """Every way this is not a routing record, not the first one."""
    problems: list[str] = []
    if not isinstance(payload, dict):
        return [f"routing: expected an object, got {type(payload).__name__}"]

    unknown_root = sorted(set(payload) - DOCUMENT_KEYS)
    if unknown_root:
        problems.append(
            f"routing: {', '.join(repr(k) for k in unknown_root)} is not part of {SCHEMA}. A "
            f"key this schema does not define would be dropped rather than honoured")

    if payload.get("schema") != SCHEMA:
        problems.append(f"schema: expected {SCHEMA!r}, got {payload.get('schema')!r}")

    for field in ("task", "strategy"):
        if not (isinstance(payload.get(field), str) and payload[field].strip()):
            problems.append(
                f"{field}: is not recorded. Without it a change in routing behaviour and a "
                f"change in the evidence look the same afterwards"
                if field == "strategy" else
                f"{field}: the record does not say which task it routed")

    assignments = payload.get("assignments")
    if not isinstance(assignments, list):
        problems.append("assignments: expected a list")
        assignments = []
    elif not assignments:
        problems.append("assignments: a routing that assigned nothing routed nothing")

    seen: set = set()
    for position, item in enumerate(assignments):
        where = f"assignments[{position}]"
        if not isinstance(item, dict):
            problems.append(f"{where}: expected an object, got {type(item).__name__}")
            continue
        unknown = sorted(set(item) - ASSIGNMENT_FIELDS)
        if unknown:
            problems.append(
                f"{where}: {', '.join(repr(k) for k in unknown)} is not part of an assignment")
        role = item.get("role")
        if role in ROLES:
            if role in seen:
                problems.append(
                    f"{where}: {role!r} is assigned more than once. Two providers for one role "
                    f"is two answers to who is accountable for it")
            else:
                seen.add(role)
        problems.extend(assignment_problems(
            role, item.get("provider"), item.get("confidence"), item.get("evidence_count"),
            item.get("reasons") if isinstance(item.get("reasons"), (list, tuple)) else
            item.get("reasons"), where))
    return problems


def load(payload: dict) -> tuple[Assignment, ...]:
    """Build the assignments from a validated record. Raises `ValueError` if it is not one."""
    problems = validate(payload)
    if problems:
        raise ValueError("not a routing record:\n  " + "\n  ".join(problems))
    return tuple(Assignment(role=item["role"], provider=item["provider"],
                            confidence=item["confidence"],
                            evidence_count=item["evidence_count"],
                            reasons=tuple(item["reasons"]))
                 for item in payload["assignments"])


#: Why a routing was refused. Named rather than described so a caller can branch on them:
#: "not approved" and "not measured enough for this role" call for different next moves.
NOT_APPROVED = "provider-is-not-policy-approved"
NOT_CAPABLE = "provider-cannot-do-this-role"
NOT_INDEPENDENT = "verifier-is-the-developer"
NOT_MEASURED = "unmeasured-provider-in-an-assurance-role"
NOT_THIS_TASK = "constraints-were-chosen-for-another-task"
IDENTITY_UNKNOWN = "provider-has-no-canonical-identity"
ROLE_UNFILLED = "required-role-has-no-provider"
ROLE_TWICE = "one-role-assigned-twice"


def violations(assignments: tuple, constraints: Constraints) -> list[dict]:
    """Every hard constraint this routing breaks, not the first one.

    These outrank whatever the routing optimised for. Cost, latency and a provider's measured
    excellence are all arguments about *which* approved, capable, independent provider to pick,
    and none of them is an argument for picking one that is not — which is the trade the
    design principle rules out and the one a cost-driven router will otherwise make.

    Returning all of them is the same rule `validate` follows: a router told only about the
    unapproved provider would swap it and meet the independence problem it was always going
    to meet.
    """
    found: list[dict] = []

    # Before `by_role`, because building it is where the problem would disappear: a dict
    # comprehension keeps the last of two developers, and the first one — still in the loop
    # below — would then be compared against a developer it is not. `validate` refuses this on
    # the JSON path; a caller assembling `Assignment`s reaches here directly, and a check on
    # one path is a check on one path.
    counts: dict = {}
    for assignment in assignments:
        counts[assignment.role] = counts.get(assignment.role, 0) + 1
    for role, count in sorted(counts.items()):
        if count > 1:
            found.append({
                "reason": ROLE_TWICE, "role": role,
                "detail": f"{role!r} is assigned {count} times. Two providers for one role is "
                          f"two answers to who is accountable for it, and comparing against "
                          f"either of them is comparing against the wrong one",
            })
    if found:
        # The remaining checks read `by_role`, and it cannot be built from this.
        return found

    by_role = {a.role: a for a in assignments}

    for role in sorted(constraints.required - set(by_role)):
        found.append({
            "reason": ROLE_UNFILLED, "role": role,
            "detail": f"{role!r} is required for this change and nothing was assigned to it. "
                      f"An unfilled role is not a cheaper team, it is a missing one",
        })

    for assignment in assignments:
        role, provider = assignment.role, assignment.provider
        if constraints.approved is not None and provider not in constraints.approved:
            found.append({
                "reason": NOT_APPROVED, "role": role, "provider": provider,
                "detail": f"{provider!r} is not among the providers this policy approves",
            })
        capable = constraints.capable.get(role)
        if capable is not None and provider not in capable:
            found.append({
                "reason": NOT_CAPABLE, "role": role, "provider": provider,
                "detail": f"{provider!r} cannot do {role!r} here — the capability is a fact "
                          f"about what it can run, not a preference to weigh",
            })
        if role in constraints.independent and DEVELOPER in by_role:
            mine = constraints.canonical(provider)
            theirs = constraints.canonical(by_role[DEVELOPER].provider)
            if mine is None or theirs is None:
                found.append({
                    "reason": IDENTITY_UNKNOWN, "role": role, "provider": provider,
                    "detail": f"this policy states canonical identities and does not name "
                              f"{provider if mine is None else by_role[DEVELOPER].provider!r}. "
                              f"Independence cannot be checked on a name nothing resolves",
                })
            elif mine == theirs:
                found.append({
                    "reason": NOT_INDEPENDENT, "role": role, "provider": provider,
                    "detail": f"{provider!r} is {mine!r}, which wrote the change and would be "
                              f"judging it. No evidence about how good it is makes that "
                              f"verdict independent",
                })
        if role in ASSURANCE_ROLES and provider not in constraints.measured.get(role, ()):
            found.append({
                "reason": NOT_MEASURED, "role": role, "provider": provider,
                "detail": f"this policy does not list {provider!r} as measured for {role!r}; "
                          f"the record calls it {assignment.confidence!r}, which is the "
                          f"router's account of itself and not the fact that unlocks the seat",
            })
    return found


#: The keys a constraints document may carry. Closed for the reason the record's are.
CONSTRAINT_KEYS = frozenset({"approved", "capable", "measured", "also_independent",
                             "required", "identity", "task"})


def load_constraints(payload: object) -> tuple:
    """A `Constraints`, or a refusal.

    `frozenset(...)` takes whatever iterates, and the constraints document is the one that
    says who is *allowed*. A JSON object becomes its keys, so `{"approved": {"evil/model":
    false}}` approves the provider it looks like it denies; a JSON string becomes its
    characters. Malformed structure acquiring valid policy meaning is the failure this whole
    module exists to prevent one level up, and the policy file is not exempt from it.
    """
    if not isinstance(payload, dict):
        raise ValueError(
            f"constraints: expected an object, got {type(payload).__name__}")
    unknown = sorted(set(payload) - CONSTRAINT_KEYS)
    if unknown:
        raise ValueError(
            f"constraints: {', '.join(repr(k) for k in unknown)} is not part of a constraint "
            f"set. A key this schema does not define would be dropped rather than honoured")

    def _listed(where: str, value: object) -> frozenset:
        """A JSON array turned into a set, with the shape checked and nothing else.

        Shape here and contents in `Constraints`: a dict becomes its keys and a string its
        characters, and only this layer can see that, because by the time either reaches the
        dataclass it is a set of things that look plausible. What is *in* the set is the
        dataclass's to judge, so that a caller building one directly gets the same answer.
        """
        if not isinstance(value, list):
            raise ValueError(
                f"constraints: {where} must be an array, got {type(value).__name__}. Anything "
                f"that iterates would otherwise become whatever it happens to yield")
        # A set: every constraint here asks whether something is in the list, so order and
        # repetition say nothing and keeping them would invite a reader to think they do.
        return frozenset(value)

    by_role = {}
    for name in ("capable", "measured"):
        raw_map = payload.get(name, {})
        if not isinstance(raw_map, dict):
            raise ValueError(
                f"constraints: {name!r} must map a role to its providers, got "
                f"{type(raw_map).__name__}")
        unknown_roles = sorted(set(raw_map) - set(ROLES))
        if unknown_roles:
            raise ValueError(
                f"constraints: {name!r} names role(s) that do not exist: "
                f"{', '.join(unknown_roles)}")
        by_role[name] = {role: _listed(f"'{name}.{role}'", providers)
                         for role, providers in raw_map.items()}

    identity_raw = payload.get("identity")
    if not isinstance(identity_raw, dict):
        raise ValueError(
            "constraints: 'identity' must map each provider name to the backend it actually "
            "is. `{}` says the names are already canonical, and it has to be said: silence "
            "would mean independence was compared on whatever the router called things")
    # Shape only; `Constraints` judges what is in it, so a caller building one directly gets
    # the same answer rather than a weaker one.
    identity = dict(identity_raw)

    constraints = Constraints(
        # Absent is "this policy states no allowlist"; present and empty is an allowlist that
        # names nobody, and the two mean opposite things.
        approved=(_listed("'approved'", payload["approved"])
                  if "approved" in payload else None),
        capable=by_role["capable"],
        measured=by_role["measured"],
        also_independent=_listed("'also_independent'", payload.get("also_independent", [])),
        identity=identity,
        required=_listed("'required'", payload.get("required", [])),
        task=payload.get("task"),
    )
    return constraints


#: What `route-check` answers.
ADMISSIBLE, REFUSED = "admissible", "refused"


def check(payload: dict, constraints: Constraints) -> dict:
    """Whether this routing may be used, and everything wrong with it if not.

    Nothing here says the team is a *good* one. Which approved, capable, independent provider
    performs best on this kind of change is a judgement about evidence, and this module has
    neither the evidence nor a way to check a conclusion drawn from it. What it says is that
    optimisation did not reach past the constraints to get its answer.
    """
    problems = validate(payload)
    if problems:
        return {
            "schema": SCHEMA, "status": REFUSED,
            "violations": [{"reason": "invalid-record", "detail": p} for p in problems],
            "strategy": None, "reported_unmeasured": [],
        }

    assignments = load(payload)
    found = violations(assignments, constraints)
    if constraints.task is not None and payload["task"] != constraints.task:
        # A record free to say what it routed picks which constraints apply to it — the
        # decision this boundary exists to keep out of the record's hands.
        found = [{"reason": NOT_THIS_TASK, "role": None,
                  "detail": f"the record routed {payload['task']!r} and these constraints were "
                            f"chosen for {constraints.task!r}. Constraints selected for a "
                            f"different change say nothing about this one"}] + found
    return {
        "schema": SCHEMA,
        "status": REFUSED if found else ADMISSIBLE,
        "violations": found,
        "strategy": payload["strategy"],
        # The *record's* word, named as such: eligibility comes from the policy, and this is
        # what the router said about providers it put in roles where the policy does not have
        # to have measured them. A reader deciding whether to trust the result should see it
        # without having to ask, and should not read it as a measurement that happened.
        "reported_unmeasured": sorted({(a.role, a.provider) for a in assignments
                                       if a.confidence == UNMEASURED}),
    }


#: What `route-team` returns. `1` covers an invalid record and a refused routing: both mean
#: the team may not be used as recorded.
ADMITTED, REJECTED, EXECUTION_ERROR = 0, 1, 2


def cmd_route_team(args) -> "NoReturn":  # noqa: F821
    """Check a routing record against the constraints a policy states.

    Exits rather than returns, for the reason `cmd_synthesis` does: the dispatcher calls
    subcommands for their effect and discards what they hand back, so a refusal that returned
    `1` would print its reasons and leave the shell believing the team was usable.
    """
    import json
    import pathlib
    import sys

    from .synthesis import _no_duplicate_keys

    try:
        payload = json.loads(pathlib.Path(args.routing).read_text(encoding="utf-8"),
                             object_pairs_hook=_no_duplicate_keys("routing"))
        # Required rather than defaulted: every constraint here is the caller's to state, and
        # a caller who forgot the file or whose policy failed to resolve would otherwise get an
        # admission that means "nothing was enforced".
        raw = json.loads(pathlib.Path(args.constraints).read_text(encoding="utf-8"),
                         object_pairs_hook=_no_duplicate_keys("constraints"))
        constraints = load_constraints(raw)
    except Exception as exc:  # noqa: BLE001 — every failure to read is one status
        print(json.dumps({"schema": SCHEMA, "status": "execution-error",
                          "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        sys.exit(EXECUTION_ERROR)

    result = check(payload, constraints)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"routing: {result['status']}"
              + (f" — strategy {result['strategy']}" if result["strategy"] else ""))
        for item in result["violations"]:
            print(f"    {item['reason']}: {item['detail']}")
        if result["reported_unmeasured"]:
            said = ", ".join(f"{provider} as {role}"
                             for role, provider in result["reported_unmeasured"])
            print(f"  the record calls these unmeasured (its word, not the policy's): {said}")
    sys.exit(ADMITTED if result["status"] == ADMISSIBLE else REJECTED)
