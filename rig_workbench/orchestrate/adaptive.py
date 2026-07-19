"""Deterministic risk analysis for adaptive bugfix review routing."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class RiskSignal:
    domain: str
    severity: int
    evidence: str

    def to_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "severity": self.severity,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class RiskAssessment:
    primary: str
    secondary: str | None
    signals: tuple[RiskSignal, ...]
    fallback_reason: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "primary": self.primary,
            "secondary": self.secondary,
            "signals": [signal.to_dict() for signal in self.signals],
            "fallback_reason": self.fallback_reason,
        }


_REVIEWERS = {
    "security": "security-reviewer",
    "design": "design-reviewer",
    "test": "test-reviewer",
}

_RULES = (
    ("security", 3, re.compile(
        r"authentication|authorization|current_user|requested_user|ownership|"
        r"\bsql\b|select\s+.+\s+from|insert\s+into|update\s+.+\s+set|"
        r"secret|api[_-]?key|password|token|subprocess|shell\s*=|exec\(|eval\(|"
        r"trust[- ]boundary|untrusted",
        re.IGNORECASE,
    )),
    ("design", 3, re.compile(
        r"public\s+api|app\.(?:get|post|put|patch|delete)\(|/v\d+/|"
        r"\bschema\b|alter\s+table|migration|dependency|requirements(?:\.txt)?|"
        r"package(?:\.json)?|configuration|config\b|structural",
        re.IGNORECASE,
    )),
    ("test", 2, re.compile(
        r"assert(?:ion)?\b|\btests?[/\\]|\.test\.|\.spec\.|boundary|"
        r"state\s*(?:transition|machine)|transition(?:ed|s)?|error\s+path|"
        r"invalid|edge\s+case",
        re.IGNORECASE,
    )),
)

_DESIGN_PATH = re.compile(
    r"(?:^|[/\\])(?:api|schema|migrations?|config(?:uration)?|settings?)(?:[/\\]|\.)|"
    r"(?:^|[/\\])(?:requirements(?:\.txt)?|package(?:\.json)?|pyproject\.toml)$",
    re.IGNORECASE,
)


def _signals(diff: str, changed_files: list[str]) -> list[RiskSignal]:
    signals = []
    for line in diff.splitlines():
        evidence = line[1:].strip() if line[:1] in "+-" else line.strip()
        if not evidence:
            continue
        for domain, severity, pattern in _RULES:
            if pattern.search(evidence):
                signals.append(RiskSignal(domain, severity, evidence))

    for path in changed_files:
        if re.search(r"(?:^|[/\\])tests?[/\\]|(?:\.test|\.spec)\.", path, re.IGNORECASE):
            signals.append(RiskSignal("test", 2, path))
        elif _DESIGN_PATH.search(path):
            signals.append(RiskSignal("design", 3, path))

    return sorted(signals, key=lambda signal: (-signal.severity, signal.domain, signal.evidence))


def analyze_diff(diff: str, changed_files: list[str]) -> RiskAssessment:
    signals = tuple(_signals(diff, changed_files))
    if not signals:
        return RiskAssessment(
            primary=_REVIEWERS["test"],
            secondary=None,
            signals=(),
            fallback_reason="no recognized risk signals",
        )

    primary_domain = signals[0].domain
    secondary_domain = next(
        (signal.domain for signal in signals
         if signal.domain != primary_domain and signal.severity >= 2),
        None,
    )
    return RiskAssessment(
        primary=_REVIEWERS[primary_domain],
        secondary=_REVIEWERS[secondary_domain] if secondary_domain else None,
        signals=signals,
        fallback_reason=None,
    )


def invocation_limit(assessment: RiskAssessment) -> int:
    return 4 if assessment.secondary is not None else 3
