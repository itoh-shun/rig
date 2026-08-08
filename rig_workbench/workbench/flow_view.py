"""Showing what the chosen recipe is going to do, to somebody who does not know it.

The registration banner said *which* recipe was chosen and never what it would do.
`bugfix` is seven steps, dispatches three reviewers in parallel at step six and judges
ten criteria at step seven — none of which appeared anywhere the user would see it.
The information existed the whole time (`orchestrate plan`); it was one command away
from the path anybody actually takes, which by rig's own taxonomy is an asset that is
present and not connected.

**Shape decides the display.** Twelve of the shipped recipes have exactly one step, so
a linear stepper would render `[▸] 1/1` for the most common case — a progress bar over
a single item, which tells nobody anything. What is complex about those runs is inside
the step: `review-only` is one step that fans out to three reviewers and waits for all
three. So a one-step recipe shows its fan-out and its gate instead of a position.

**Frequency decides the budget.** Per-turn output stays one line (SKILL.md §6 caps it
there for a reason, and rig's own context metering now counts what it spends). Step
transitions happen ~7 times a run and can afford five lines. The full map prints once.
"""

from __future__ import annotations

from .progress import BAR_CURRENT, Progress

# Steps whose gate makes them a hard stop, and the phrase for each.
_GATE_LABEL = {
    "acceptance-gate": "受け入れ基準で機械判定",
    "review-gate": "レビュー判定が揃うまで進まない",
}
_STOP = "◆"


def _persona_summary(step: dict) -> str:
    personas = step.get("personas") or []
    if not personas:
        return ""
    if len(personas) == 1:
        return personas[0]
    return f"{len(personas)}人並列: " + " / ".join(p.split("/")[-1] for p in personas)


def _step_line(index: int, step: dict, marker: str = " ") -> str:
    gate = step.get("gate")
    bits = []
    if gate:
        bits.append(_GATE_LABEL.get(gate, gate))
    who = _persona_summary(step)
    if who:
        bits.append(who)
    if step.get("human_gate"):
        owner = step.get("actor")
        bits.append(f"人の署名待ちで停止（{owner}）" if owner else "人の署名待ちで停止")
    stop = _STOP if (gate or step.get("human_gate")) else " "
    detail = "  ".join(bits)
    return f"  {marker} {index} {step['name']:<22} {stop} {detail}".rstrip()


def _criteria_count(acceptance: dict) -> int:
    return len(acceptance.get("checks") or [])


def render_flow(steps: list[dict], acceptance: dict) -> list[str]:
    """The map, printed once at registration. Empty when the recipe could not be read."""
    real = [s for s in steps if s.get("name")]
    if not real:
        return []
    if len(real) == 1:
        return _render_single(real[0], acceptance)
    return _render_linear(real, acceptance)


def _render_linear(steps: list[dict], acceptance: dict) -> list[str]:
    out = ["", f"flow: {len(steps)} steps"]
    for index, step in enumerate(steps, 1):
        marker = BAR_CURRENT if index == 1 else " "
        out.append(_step_line(index, step, marker))
        if step.get("condition"):
            out.append(f"       条件つき: {step['condition']}（満たさなければスキップ）")
    criteria = _criteria_count(acceptance)
    if criteria:
        out.append(f"  {_STOP} = ここを通らないと先に進めない"
                   f"（最終ゲートは {criteria} 基準）")
    out.append("  あなたの出番: 全 step 通過後。差分を見て accept か discard"
               "（それまで作業ツリーは無傷）")
    return out


def _render_single(step: dict, acceptance: dict) -> list[str]:
    """One-step recipes: the position is trivial, the inside is not.

    Twelve shipped recipes land here. Rendering `1/1` for them would be the literal
    definition of a number that carries no information, so the fan-out and the gate —
    the things that actually determine when the step ends — take its place.
    """
    out = ["", f"flow: 1 step — {step['name']}"]
    personas = step.get("personas") or []
    if len(personas) > 1:
        out.append(f"  {len(personas)}人が並列でレビューし、全員の判定が揃うまで終わりません")
        for persona in personas:
            out.append(f"    ├ {persona.split('/')[-1]}")
    elif personas:
        out.append(f"  担当: {personas[0]}")
    gate = step.get("gate")
    if gate:
        out.append(f"  {_STOP} {_GATE_LABEL.get(gate, gate)}")
    criteria = _criteria_count(acceptance)
    if criteria:
        out.append(f"  受け入れ基準: {criteria} 件")
    out.append("  あなたの出番: 判定が出たら、所見を読んで対応を決める")
    return out


def render_transition(progress: Progress) -> list[str]:
    """Printed when a step actually changes state — roughly seven times a run.

    Shows where the run now is and what is coming, because a bar alone says how far
    but never toward what. A retry gets its attempt counted separately from the
    position: "7/7" that will not finish reads as a stuck run, and the reason it is
    not finishing is the attempt number.
    """
    if not progress.known:
        return []
    current = progress.current
    if current is None:
        return [f"  [{progress.bar}] {progress.total}/{progress.total} 全 step 完了"
                " → 差分を見て accept か discard"]
    out = [f"  [{progress.bar}] {progress.done}/{progress.total} → {current['name']}"]
    detail = _step_line(progress.done + 1, current, BAR_CURRENT).lstrip()
    if detail.strip():
        out.append(f"      {detail}")
    if current.get("status") == "failed":
        out.append("      ↻ やり直し中。基準を満たすまで先へ進みません")
    if progress.nxt:
        out.append(f"      次: {progress.nxt['name']}")
    return out
