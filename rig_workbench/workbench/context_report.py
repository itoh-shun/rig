"""`workbench.py context` — what rig has been spending of the parent's context.

Answers the question `context-minimal` has always asserted an answer to without ever
measuring it: how much of the parent session did rig's own output consume, and which
commands did it. Read-only over `.rig/context.jsonl`.

It also judges what it measured against the budgets `context_meter` declares, and
prints each budget beside its verdict. It does not report a dispatch rate: no signal
for that exists (see `context_meter`), and the report says so in the body rather than
letting a reader assume the missing axis was clean.
"""

from __future__ import annotations

import argparse
import datetime

from .. import context_meter
from .state import repo_root


def _span(first_ts: str, last_ts: str) -> str:
    """How long a task kept printing at the parent, or nothing at all.

    Silent on records written before timestamps were kept and on anything it cannot
    parse. A span is context for the byte count, never a reason to lose the byte count.
    """
    if not first_ts or not last_ts or first_ts == last_ts:
        return ""
    try:
        elapsed = (datetime.datetime.fromisoformat(last_ts)
                   - datetime.datetime.fromisoformat(first_ts))
    except (TypeError, ValueError):
        return ""
    minutes = int(elapsed.total_seconds() // 60)
    if minutes < 1:
        return ""
    if minutes < 60:
        return f", over {minutes}m"
    return f", over {minutes // 60}h{minutes % 60:02d}m"


def cmd_context(args: argparse.Namespace) -> None:
    root = repo_root()
    records = context_meter.load(root, since_days=args.since_days)
    if not records:
        print("## rig context\n")
        print("No records yet. Every rig command counts its own output from now on;")
        print("run a task and check back (`.rig/context.jsonl`).")
        return

    summary = context_meter.summarize(records)
    total = summary["bytes"]
    window = f"last {args.since_days} days" if args.since_days else "all time"
    print(f"## rig context ({window})\n")
    print(f"rig printed {context_meter.human(total)} at the parent session "
          f"across {summary['calls']} invocation(s)")
    print(f"  ≈ {context_meter.approx_tokens(total):,} tokens (rough: ~4 bytes/token)")
    print()
    print("This is rig's own output only — what rig printed and the parent had to read.")
    print("It is not the session's total context: files the parent opened itself, the")
    print("conversation, and every Bash, Read and Grep the parent ran on its own are")
    print("invisible from here. What it does show is the part rig controls.")
    print()
    print("Dispatch is invisible too, and that was checked rather than assumed: Claude")
    print("Code hands a subagent's shell the same environment it hands the parent's —")
    print("same variables, same session id — so nothing here can tell you whether a rig")
    print("command ran in a subagent or in the parent thread. No dispatch rate is")
    print("reported because a guessed one would be worse than none.")

    print("\n### budget")
    for verdict in context_meter.budget_verdicts(records, summary):
        status = "ok  " if verdict["over"] == 0 else "over"
        print(f"  [{status}] {verdict['label']:<18} "
              f"budget {context_meter.human(verdict['budget'])}, "
              f"{verdict['over']}/{verdict['checked']} {verdict['unit']} over, "
              f"worst {context_meter.human(verdict['worst'])}")
    print()
    print("  Both budgets are conventions rig declares, not limits it enforces: nothing")
    print("  reads this verdict, no gate fails on it. And `ok` is narrow — it means rig")
    print("  stayed inside its own output budget. It says nothing about how the parent")
    print("  spent the rest of its context, because only rig-wb invocations are counted")
    print("  here; a session that burned itself out on two thousand raw greps still")
    print("  reads `ok` on this line.")

    print("\n### by command (biggest first)")
    for command, entry in list(summary["by_command"].items())[:12]:
        share = (entry["bytes"] / total * 100) if total else 0
        print(f"  {context_meter.human(entry['bytes']):>8}  {share:4.0f}%  "
              f"{command:<28} {entry['calls']:>3} call(s), "
              f"largest {context_meter.human(entry['max'])}")

    if summary["by_task"]:
        print("\n### by task (biggest first)")
        for task_id, entry in list(summary["by_task"].items())[:10]:
            span = _span(entry["first_ts"], entry["last_ts"])
            print(f"  {context_meter.human(entry['bytes']):>8}  {task_id:<34} "
                  f"{entry['calls']:>3} call(s), "
                  f"largest {context_meter.human(entry['max'])}{span}")

    heavy = [r for r in records if r.get("bytes", 0) >= context_meter.NOTABLE_BYTES]
    if heavy:
        heavy.sort(key=lambda r: -r.get("bytes", 0))
        print(f"\n### single invocations over {context_meter.human(context_meter.NOTABLE_BYTES)}")
        for record in heavy[:8]:
            print(f"  {context_meter.human(record['bytes']):>8}  {record.get('ts', '?')}  "
                  f"{record.get('command', '?')}")
        print("\n  These are the ones worth trimming first — one command that dumps a")
        print("  diff into the parent outweighs forty cheap status calls.")
