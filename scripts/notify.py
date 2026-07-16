#!/usr/bin/env python3
"""
rig notify — Slack/Teams webhook notifications (#287)

A thin, opt-in script that posts accept-pending / REJECT / escalation-style
events to an incoming webhook URL. No dedicated SDK is brought in (stdlib
urllib only) — the Slack/Teams URL is whatever the user issued on their end.

Usage:
  python3 scripts/notify.py --webhook <URL> --format slack --message "accept pending"
  python3 scripts/notify.py --webhook <URL> --format teams --title "rig" --message "..."
  python3 scripts/notify.py --format slack --message "..." --dry-run   # inspect the payload, don't send

The RIG_NOTIFY_WEBHOOK environment variable lets you omit --webhook.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def build_payload(fmt: str, title: str, message: str) -> dict:
    if fmt == "slack":
        text = f"*{title}*\n{message}" if title else message
        return {"text": text}
    if fmt == "teams":
        return {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "summary": title or "rig notification",
            "title": title or "rig notification",
            "text": message,
        }
    raise ValueError(f"unsupported format: {fmt}")


def send(webhook_url: str, payload: dict, timeout: float = 10) -> tuple[int | None, str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="ignore")
    except urllib.error.URLError as e:
        return None, str(e.reason)


def main() -> None:
    ap = argparse.ArgumentParser(description="rig notify — Slack/Teams webhook notifications")
    ap.add_argument("--webhook", default=os.environ.get("RIG_NOTIFY_WEBHOOK"),
                    help="incoming webhook URL (defaults to the RIG_NOTIFY_WEBHOOK env var)")
    ap.add_argument("--format", choices=("slack", "teams"), required=True)
    ap.add_argument("--title", default="", help="Teams-format title (Slack folds it into the message body)")
    ap.add_argument("--message", required=True)
    ap.add_argument("--dry-run", action="store_true", help="print the payload without sending it")
    args = ap.parse_args()

    payload = build_payload(args.format, args.title, args.message)

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not args.webhook:
        print("[ERROR] neither --webhook nor RIG_NOTIFY_WEBHOOK is set. Use --dry-run to inspect the payload only.",
              file=sys.stderr)
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)

    status, body = send(args.webhook, payload)
    if status is not None and 200 <= status < 300:
        print(f"✓ notification sent (status={status})")
    else:
        print(f"[ERROR] notification failed (status={status}): {body[:200]}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
