"""`workbench.py context` — what rig has been spending of the parent's context.

Answers the question `context-minimal` has always asserted an answer to without ever
measuring it: how much of the parent session did rig's own output consume, and which
commands did it. Read-only over `.rig/context.jsonl`.
"""

from __future__ import annotations

import argparse

from .. import context_meter
from .state import repo_root


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
    print("conversation, and whether the work was dispatched to a subagent are all")
    print("invisible from here. What it does show is the part rig controls.")

    print("\n### by command (biggest first)")
    for command, entry in list(summary["by_command"].items())[:12]:
        share = (entry["bytes"] / total * 100) if total else 0
        print(f"  {context_meter.human(entry['bytes']):>8}  {share:4.0f}%  "
              f"{command:<28} {entry['calls']:>3} call(s), "
              f"largest {context_meter.human(entry['max'])}")

    if summary["by_task"]:
        print("\n### by task (biggest first)")
        for task_id, size in list(summary["by_task"].items())[:10]:
            print(f"  {context_meter.human(size):>8}  {task_id}")

    heavy = [r for r in records if r.get("bytes", 0) >= context_meter.NOTABLE_BYTES]
    if heavy:
        heavy.sort(key=lambda r: -r.get("bytes", 0))
        print(f"\n### single invocations over {context_meter.human(context_meter.NOTABLE_BYTES)}")
        for record in heavy[:8]:
            print(f"  {context_meter.human(record['bytes']):>8}  {record.get('ts', '?')}  "
                  f"{record.get('command', '?')}")
        print("\n  These are the ones worth trimming first — one command that dumps a")
        print("  diff into the parent outweighs forty cheap status calls.")
