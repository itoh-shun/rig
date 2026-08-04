"""Executable gate registry shared by runtime and validators.

Only gates with code-backed handlers belong here.  A prompt pattern may describe
another aggregation contract, but it must never become executable merely because
a Markdown file with the same name exists.
"""

RUNTIME_GATES = frozenset({"acceptance-gate", "review-gate"})


def is_runtime_gate(value: object) -> bool:
    return isinstance(value, str) and value in RUNTIME_GATES
