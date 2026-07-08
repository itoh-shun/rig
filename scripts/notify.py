#!/usr/bin/env python3
"""
rig notify — Slack/Teams webhook通知（#287）

acceptance-gate待ち・REJECT・エスカレーション等のイベントを、opt-inのincoming
webhook URLへ通知する薄いスクリプト。専用SDKは持ち込まない（標準ライブラリの
urllibのみ）——Slack/TeamsのURLはユーザー側で発行したものをそのまま使う。

使い方:
  python3 scripts/notify.py --webhook <URL> --format slack --message "accept待ちです"
  python3 scripts/notify.py --webhook <URL> --format teams --title "rig" --message "..."
  python3 scripts/notify.py --format slack --message "..." --dry-run   # 送信せずpayload確認

環境変数 RIG_NOTIFY_WEBHOOK があれば --webhook を省略できる。
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
    raise ValueError(f"未対応のformat: {fmt}")


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
    ap = argparse.ArgumentParser(description="rig notify — Slack/Teams webhook通知")
    ap.add_argument("--webhook", default=os.environ.get("RIG_NOTIFY_WEBHOOK"),
                    help="incoming webhook URL（省略時は環境変数 RIG_NOTIFY_WEBHOOK）")
    ap.add_argument("--format", choices=("slack", "teams"), required=True)
    ap.add_argument("--title", default="", help="Teams形式のtitle（Slackは本文に埋め込む）")
    ap.add_argument("--message", required=True)
    ap.add_argument("--dry-run", action="store_true", help="送信せずpayloadを表示するだけ")
    args = ap.parse_args()

    payload = build_payload(args.format, args.title, args.message)

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not args.webhook:
        print("[ERROR] --webhook も RIG_NOTIFY_WEBHOOK も未設定です。--dry-run でpayloadだけ確認できます。",
              file=sys.stderr)
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)

    status, body = send(args.webhook, payload)
    if status is not None and 200 <= status < 300:
        print(f"✓ 通知送信成功（status={status}）")
    else:
        print(f"[ERROR] 通知送信失敗（status={status}）: {body[:200]}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
